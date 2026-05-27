# -*- coding: utf-8 -*-
"""Запись результатов observer в БД: catalog + метрики + FSM + outbox.

Транзакционная единица одного объявления: upsert ad/campaign/adset → INSERT
ad_metrics → upsert ad_alert_state → INSERT alert_events → create disable task.

NB: метрики кладутся в партиционированную таблицу ad_metrics — партиция текущего
месяца должна существовать (создаётся cleanup_worker'ом или apply_v2_schema).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.state_machine import FsmTransition
from core.tasks import create_task

logger = logging.getLogger(__name__)


@dataclass
class PersistedRow:
    """Что записали для одного ad'а — для логов/мониторинга."""

    fb_ad_id: str
    ad_id: uuid.UUID
    transition: FsmTransition
    metric_inserted: bool = False
    disable_task_id: int | None = None


async def upsert_catalog_hierarchy(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
    ad_name: str,
    fb_adset_id: str | None,
    adset_name: str,
    fb_campaign_id: str | None,
    campaign_name: str,
    offer_id: uuid.UUID | None,
) -> uuid.UUID:
    """UPSERT offer → campaign → adset → ad, возвращает fb_ads.id (UUID).

    Все три уровня каталога обновляют last_seen_at — это позволяет отличить «живые»
    объявления от исторических.
    """
    now = datetime.now(timezone.utc)

    async with engine.begin() as conn:
        # campaign
        cmp_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_campaigns
                        (fb_campaign_id, campaign_name, offer_id, last_seen_at)
                    VALUES (:fbcid, :cname, :oid, :now)
                    ON CONFLICT (campaign_name) DO UPDATE
                    SET last_seen_at = :now,
                        fb_campaign_id = COALESCE(EXCLUDED.fb_campaign_id, fb_campaigns.fb_campaign_id),
                        offer_id = COALESCE(EXCLUDED.offer_id, fb_campaigns.offer_id),
                        is_active = TRUE
                    RETURNING id
                    """
                ),
                {
                    "fbcid": fb_campaign_id,
                    "cname": campaign_name,
                    "oid": offer_id,
                    "now": now,
                },
            )
        ).first()
        campaign_id = cmp_row[0]

        # adset
        ads_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_adsets
                        (campaign_id, fb_adset_id, adset_name, last_seen_at)
                    VALUES (:cid, :fbas, :aname, :now)
                    ON CONFLICT (campaign_id, adset_name) DO UPDATE
                    SET last_seen_at = :now,
                        fb_adset_id = COALESCE(EXCLUDED.fb_adset_id, fb_adsets.fb_adset_id),
                        is_active = TRUE
                    RETURNING id
                    """
                ),
                {
                    "cid": campaign_id,
                    "fbas": fb_adset_id,
                    "aname": adset_name,
                    "now": now,
                },
            )
        ).first()
        adset_id = ads_row[0]

        # ad
        ad_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_ads (adset_id, fb_ad_id, ad_name, last_seen_at)
                    VALUES (:adsid, :fbid, :aname, :now)
                    ON CONFLICT (fb_ad_id) DO UPDATE
                    SET last_seen_at = :now,
                        ad_name = EXCLUDED.ad_name,
                        is_active = TRUE
                    RETURNING id
                    """
                ),
                {
                    "adsid": adset_id,
                    "fbid": fb_ad_id,
                    "aname": ad_name,
                    "now": now,
                },
            )
        ).first()
    return ad_row[0]


async def insert_metrics(
    engine: AsyncEngine,
    *,
    ad_id: uuid.UUID,
    cycle_ts: datetime,
    scan_id: int | None,
    metrics: dict[str, Any],
) -> bool:
    """INSERT в партиционированную ad_metrics. Идемпотентен по (ad_id, cycle_ts).

    UNIQUE constraint (ad_id, cycle_ts) защищает от двойной записи если scan
    зашёл дважды в одну минуту.
    """
    column_names = (
        "spend",
        "reach",
        "impressions",
        "clicks",
        "cpc",
        "ctr",
        "cost_per_result",
        "cpm",
        "frequency",
        "leads",
        "cost_per_lead",
        "registrations",
        "cost_per_registration",
        "deposits",
        "outbound_clicks",
        "outbound_ctr",
        "landing_page_views",
        "cost_per_landing_page_view",
    )
    placeholders = ", ".join(f":{c}" for c in column_names)
    columns_csv = ", ".join(column_names)

    params: dict[str, Any] = {
        "ad_id": ad_id,
        "cycle_ts": cycle_ts,
        "scan_id": scan_id,
    }
    for c in column_names:
        params[c] = metrics.get(c)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"""
                    INSERT INTO ad_metrics
                        (ad_id, cycle_ts, scan_id, {columns_csv})
                    VALUES
                        (:ad_id, :cycle_ts, :scan_id, {placeholders})
                    ON CONFLICT (ad_id, cycle_ts) DO NOTHING
                    """
                ),
                params,
            )
        return True
    except Exception as exc:
        logger.warning(
            "insert_metrics для ad_id=%s упал: %s — возможно нет партиции на месяц",
            ad_id,
            exc,
        )
        return False


async def apply_fsm_transition(
    engine: AsyncEngine,
    *,
    ad_id: uuid.UUID,
    transition: FsmTransition,
    metrics_snapshot: dict[str, Any],
    scan_id: int | None,
) -> None:
    """UPSERT в ad_alert_state + INSERT в alert_events если был emit.

    Один atomic commit — FSM trans + event log не должны разойтись.
    """
    async with engine.begin() as conn:
        # 1. upsert ad_alert_state
        await conn.execute(
            text(
                """
                INSERT INTO ad_alert_state
                    (ad_id, alert_state, current_stage, open_state_token,
                     warning_rule_codes, stop_rule_codes,
                     last_scan_id, last_transition_at)
                VALUES
                    (:aid, :st, :stage, :tok,
                     CAST(:wrc AS JSONB), CAST(:src AS JSONB),
                     :scan_id, NOW())
                ON CONFLICT (ad_id) DO UPDATE
                SET alert_state = EXCLUDED.alert_state,
                    current_stage = EXCLUDED.current_stage,
                    open_state_token = EXCLUDED.open_state_token,
                    warning_rule_codes = EXCLUDED.warning_rule_codes,
                    stop_rule_codes = EXCLUDED.stop_rule_codes,
                    last_scan_id = EXCLUDED.last_scan_id,
                    last_transition_at = CASE
                        WHEN ad_alert_state.alert_state != EXCLUDED.alert_state
                        THEN NOW() ELSE ad_alert_state.last_transition_at
                    END
                """
            ),
            {
                "aid": ad_id,
                "st": transition.new_state,
                "stage": transition.new_stage,
                "tok": transition.new_open_token,
                "wrc": json.dumps(list(_warnings_from(transition))),
                "src": json.dumps(list(_stops_from(transition))),
                "scan_id": scan_id,
            },
        )

        # 2. INSERT в alert_events если был emit
        if transition.emit_alert and transition.alert_stage is not None:
            await conn.execute(
                text(
                    """
                    INSERT INTO alert_events
                        (ad_id, stage, state, matched_rule_codes,
                         metrics_json, open_state_token, scan_id)
                    VALUES
                        (:aid, :stage, :state, CAST(:mrc AS JSONB),
                         CAST(:m AS JSONB), :tok, :scan_id)
                    """
                ),
                {
                    "aid": ad_id,
                    "stage": transition.alert_stage,
                    "state": transition.new_state,
                    "mrc": json.dumps(list(transition.alert_rule_codes)),
                    "m": json.dumps(_serialize_metrics(metrics_snapshot)),
                    "tok": transition.new_open_token,
                    "scan_id": scan_id,
                },
            )


def _warnings_from(transition: FsmTransition) -> tuple[str, ...]:
    return transition.alert_rule_codes if transition.alert_stage == "warning" else ()


def _stops_from(transition: FsmTransition) -> tuple[str, ...]:
    return transition.alert_rule_codes if transition.alert_stage == "stop" else ()


def _serialize_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Decimal → str для JSON-сериализации (asyncpg + JSONB)."""
    out: dict[str, Any] = {}
    for k, v in metrics.items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


async def maybe_create_disable_task(
    engine: AsyncEngine,
    *,
    transition: FsmTransition,
    fb_ad_id: str,
    open_token: uuid.UUID | None,
) -> int | None:
    """Если FSM решил создать disable-task — отправляем в task_queue.

    idempotency_key = "auto:{fb_ad_id}:{open_token}" — гарантирует одну задачу
    на инцидент (если open_token тот же).
    """
    if not transition.create_disable_task:
        return None
    token = open_token or transition.new_open_token or uuid.uuid4()
    key = f"auto:disable:{fb_ad_id}:{token}"
    task_id = await create_task(
        engine,
        task_type="disable",
        idempotency_key=key,
        payload={"fb_ad_id": fb_ad_id, "open_state_token": str(token)},
        requested_by="bot_auto_stop",
    )
    return task_id


async def reset_alert_state_after_disable_succeeded(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
) -> bool:
    """Перевести ad_alert_state в 'disabled' после успешного disable.

    Идемпотентно: UPDATE срабатывает только если текущий state ∈
    (warning_sent, stop_sent, claimed). Если уже 'disabled' — no-op.
    Из 'normal' не возвращаем в 'disabled': observer мог сбросить state
    после реактивации, и его решение приоритетнее лагающей disable-задачи.

    Returns: True если строку обновили, False если no-op (или ad не найден).
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'disabled',
                    last_transition_at = NOW(),
                    updated_at = NOW()
                WHERE ad_id = (SELECT id FROM fb_ads WHERE fb_ad_id = :fbid)
                  AND alert_state IN ('warning_sent', 'stop_sent', 'claimed')
                """
            ),
            {"fbid": fb_ad_id},
        )
        return bool(result.rowcount and result.rowcount > 0)


async def reset_alert_state_after_enable_succeeded(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
) -> bool:
    """Перевести ad_alert_state в 'normal' после успешного enable.

    Сбрасывает FSM-контекст: open_state_token, current_stage, коды правил,
    snoozed_until. Идемпотентно: UPDATE срабатывает только если state != 'normal'.

    Returns: True если строку обновили, False если уже в 'normal' или ad не найден.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'normal',
                    current_stage = NULL,
                    open_state_token = NULL,
                    warning_rule_codes = '[]'::jsonb,
                    stop_rule_codes = '[]'::jsonb,
                    snoozed_until = NULL,
                    last_transition_at = NOW(),
                    updated_at = NOW()
                WHERE ad_id = (SELECT id FROM fb_ads WHERE fb_ad_id = :fbid)
                  AND alert_state <> 'normal'
                """
            ),
            {"fbid": fb_ad_id},
        )
        return bool(result.rowcount and result.rowcount > 0)
