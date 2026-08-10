# -*- coding: utf-8 -*-
"""Запись результатов observer в БД: catalog + метрики + FSM + outbox.

Транзакционная единица одного объявления: upsert ad/campaign/adset → INSERT
ad_metrics → upsert ad_alert_state → INSERT alert_events → create disable task.

NB: метрики кладутся в партиционированную таблицу ad_metrics — партиция текущего
месяца должна существовать (создаётся cleanup_worker'ом или apply_schema).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.commands import CommandService
from core.meta_api.autostop_alert import UNDELIVERED_INCIDENT_KEY_PREFIX
from core.meta_api.identity import require_ad_account_id
from core.money import validated_currency_code
from core.observer.cabinet_supervisor import CabinetLease
from core.observer.state_machine import FsmTransition
from core.telegram.notifications import enqueue_notification_in_transaction
from core.telegram.schemas import (
    NotificationActionSpec,
    NotificationCardFacts,
    NotificationEventSpec,
)
from core.telegram.worker_notify import (
    notify_owners_in_transaction,
    resolve_recurring_incident_in_transaction,
)

logger = logging.getLogger(__name__)
_WARNING_RISK_GROWTH_FACTOR = Decimal("1.25")
_MONEY_RULE_CODES = frozenset(
    {
        "cpc_stop",
        "cpl_stop",
        "cpr_stop",
        "spend_no_dep_range",
        "spend_with_dep_range",
    }
)


def _require_canonical_meta_id(value: str | None, *, field_name: str) -> str:
    object_name = field_name.removeprefix("fb_").replace("_", " ")
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdigit():
        raise ValueError(f"{field_name} must be a canonical numeric Meta {object_name}")
    return value


class CabinetFenceRejected(RuntimeError):
    """The observer actor no longer owns the cabinet write fence."""


async def _lock_and_assert_cabinet_fence(
    conn: AsyncConnection,
    lease: CabinetLease | None,
) -> None:
    """Validate the cabinet lease inside the same transaction as the write.

    ``FOR UPDATE`` serializes this check with lease takeover. A new actor cannot
    advance the fencing token until the current write commits, and a stale actor
    cannot mutate observer state after a takeover has committed.
    """
    if lease is None:
        return
    owned = (
        await conn.execute(
            text(
                """
                SELECT 1
                FROM cabinet_runtime
                WHERE ad_account_id = :account
                  AND owner_instance = :owner
                  AND lease_token = :token
                  AND lease_expires_at > clock_timestamp()
                FOR UPDATE
                """
            ),
            {
                "account": lease.ad_account_id,
                "owner": lease.owner_instance,
                "token": lease.lease_token,
            },
        )
    ).first()
    if owned is None:
        raise CabinetFenceRejected(f"cabinet fence rejected for account={lease.ad_account_id}")


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
    fb_adset_id: str,
    adset_name: str,
    fb_campaign_id: str,
    campaign_name: str,
    offer_id: uuid.UUID | None,
    delivery_status: str | None = None,
    ad_account_id: str,
    creative_thumb_url: str | None = None,
    creative_image_url: str | None = None,
    adset_pixel_id: str | None = None,
    adset_daily_budget: str | None = None,
    adset_lifetime_budget: str | None = None,
    adset_budget_remaining: str | None = None,
    adset_learning_stage: str | None = None,
    cabinet_lease: CabinetLease | None = None,
) -> uuid.UUID:
    """UPSERT offer → campaign → adset → ad, возвращает fb_ads.id (UUID).

    Все три уровня каталога обновляют last_seen_at — это позволяет отличить «живые»
    объявления от исторических.

    delivery_status — текущий статус доставки ad'а из скана; пустую строку
    нормализуем в NULL (фронту не нужен "" вместо реального статуса).

    fb_ad_id / fb_adset_id / fb_campaign_id — обязательные канонические Meta IDs
    (только ASCII-цифры). Строки без ID не создаются и не усыновляются по имени.

    ad_account_id — обязательный кабинет, из которого пришла строка скана.

    creative_* / adset_* (Волна 1) — превью крео + метаданные адсета. Пустые
    нормализуем в NULL и COALESCE'им: скан без значения не затирает уже известное
    (URL крео обновляется только когда Graph его отдал).
    """
    # Пустой/пробельный статус → NULL: пишем только осмысленное значение.
    delivery_status = delivery_status.strip() if delivery_status else None
    delivery_status = delivery_status or None
    ad_account_id = require_ad_account_id(ad_account_id)
    fb_ad_id = _require_canonical_meta_id(fb_ad_id, field_name="fb_ad_id")
    fb_adset_id = _require_canonical_meta_id(fb_adset_id, field_name="fb_adset_id")
    fb_campaign_id = _require_canonical_meta_id(
        fb_campaign_id,
        field_name="fb_campaign_id",
    )

    # Волна 1: пустые строки → NULL (COALESCE ниже не затирает известное значение).
    def _nz(value: str | None) -> str | None:
        return (value or "").strip() or None

    creative_thumb_url = _nz(creative_thumb_url)
    creative_image_url = _nz(creative_image_url)
    adset_pixel_id = _nz(adset_pixel_id)
    adset_daily_budget = _nz(adset_daily_budget)
    adset_lifetime_budget = _nz(adset_lifetime_budget)
    adset_budget_remaining = _nz(adset_budget_remaining)
    adset_learning_stage = _nz(adset_learning_stage)
    now = datetime.now(timezone.utc)

    async with engine.begin() as conn:
        await _lock_and_assert_cabinet_fence(conn, cabinet_lease)
        # --- campaign: идентичность = fb_campaign_id ---
        # Одноимённые кампании РАЗНЫХ кабинетов — разные строки; upsert по имени
        # сливал их в одну (ads обоих кабинетов цеплялись к ней, ad_account_id прыгал).
        params = {
            "fbcid": fb_campaign_id,
            "cname": campaign_name,
            "oid": offer_id,
            "now": now,
            "acct": ad_account_id,
        }
        cmp_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_campaigns
                        (fb_campaign_id, campaign_name, offer_id, last_seen_at, ad_account_id)
                    VALUES (:fbcid, :cname, :oid, :now, :acct)
                    ON CONFLICT (fb_campaign_id) WHERE fb_campaign_id IS NOT NULL
                    DO UPDATE
                    SET last_seen_at = :now,
                        campaign_name = EXCLUDED.campaign_name,
                        offer_id = COALESCE(EXCLUDED.offer_id, fb_campaigns.offer_id),
                        ad_account_id = EXCLUDED.ad_account_id,
                        is_active = TRUE
                    RETURNING id
                    """
                ),
                params,
            )
        ).first()
        campaign_id = cmp_row[0]

        # adset (+ Волна 1: pixel/budgets/learning — COALESCE не затирает известное NULL'ом)
        ads_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_adsets
                        (campaign_id, fb_adset_id, adset_name, last_seen_at,
                         pixel_id, daily_budget, lifetime_budget,
                         budget_remaining, learning_stage)
                    VALUES (:cid, :fbas, :aname, :now,
                            :pixel, :dbud, :lbud, :brem, :lstage)
                    ON CONFLICT (fb_adset_id) WHERE fb_adset_id IS NOT NULL
                    DO UPDATE
                    SET last_seen_at = :now,
                        campaign_id = EXCLUDED.campaign_id,
                        adset_name = EXCLUDED.adset_name,
                        pixel_id = COALESCE(EXCLUDED.pixel_id, fb_adsets.pixel_id),
                        daily_budget = COALESCE(EXCLUDED.daily_budget, fb_adsets.daily_budget),
                        lifetime_budget =
                            COALESCE(EXCLUDED.lifetime_budget, fb_adsets.lifetime_budget),
                        budget_remaining =
                            COALESCE(EXCLUDED.budget_remaining, fb_adsets.budget_remaining),
                        learning_stage =
                            COALESCE(EXCLUDED.learning_stage, fb_adsets.learning_stage),
                        is_active = TRUE
                    RETURNING id
                    """
                ),
                {
                    "cid": campaign_id,
                    "fbas": fb_adset_id,
                    "aname": adset_name,
                    "now": now,
                    "pixel": adset_pixel_id,
                    "dbud": adset_daily_budget,
                    "lbud": adset_lifetime_budget,
                    "brem": adset_budget_remaining,
                    "lstage": adset_learning_stage,
                },
            )
        ).first()
        adset_id = ads_row[0]

        # ad (+ Волна 1: превью крео — COALESCE не затирает известный URL пустым сканом)
        ad_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_ads
                        (adset_id, fb_ad_id, ad_name, delivery_status, last_seen_at,
                         creative_thumb_url, creative_image_url)
                    VALUES (:adsid, :fbid, :aname, :dstatus, :now, :cthumb, :cimage)
                    ON CONFLICT (fb_ad_id) DO UPDATE
                    SET last_seen_at = :now,
                        adset_id = EXCLUDED.adset_id,
                        ad_name = EXCLUDED.ad_name,
                        delivery_status = EXCLUDED.delivery_status,
                        creative_thumb_url =
                            COALESCE(EXCLUDED.creative_thumb_url, fb_ads.creative_thumb_url),
                        creative_image_url =
                            COALESCE(EXCLUDED.creative_image_url, fb_ads.creative_image_url),
                        is_active = TRUE
                    RETURNING id
                    """
                ),
                {
                    "adsid": adset_id,
                    "fbid": fb_ad_id,
                    "aname": ad_name,
                    "dstatus": delivery_status,
                    "now": now,
                    "cthumb": creative_thumb_url,
                    "cimage": creative_image_url,
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
    currency: str,
    metrics: dict[str, Any],
    cabinet_lease: CabinetLease | None = None,
) -> bool:
    """INSERT в партиционированную ad_metrics. Идемпотентен по (ad_id, cycle_ts).

    UNIQUE constraint (ad_id, cycle_ts) защищает от двойной записи если scan
    зашёл дважды в одну минуту.
    """
    confirmed_currency = validated_currency_code(currency)
    if confirmed_currency is None:
        raise ValueError("ad_metrics currency must be confirmed")

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
        "currency": confirmed_currency,
    }
    for c in column_names:
        params[c] = metrics.get(c)

    try:
        async with engine.begin() as conn:
            await _lock_and_assert_cabinet_fence(conn, cabinet_lease)
            await conn.execute(
                text(
                    f"""
                    INSERT INTO ad_metrics
                        (ad_id, cycle_ts, scan_id, currency, {columns_csv})
                    VALUES
                        (:ad_id, :cycle_ts, :scan_id, :currency, {placeholders})
                    ON CONFLICT (ad_id, cycle_ts) DO NOTHING
                    """
                ),
                params,
            )
        return True
    except CabinetFenceRejected:
        raise
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
    fb_ad_id: str | None = None,
    ad_account_id: str | None = None,
    currency: str,
    cabinet_lease: CabinetLease | None = None,
) -> int | None:
    """UPSERT в ad_alert_state + INSERT в alert_events если был emit.

    Один atomic commit — FSM trans + event log не должны разойтись.

    WHERE-guard в DO UPDATE: observer НЕ затирает терминальные состояния
    (claimed / disabled). Эти состояния выставляет CommandService при принятии
    команды и Meta API worker после подтверждённого disable. Их
    обратный сброс в normal/warning_sent/stop_sent делается отдельными
    функциями reset_alert_state_after_*_succeeded + observer-reopen логикой.
    """
    confirmed_currency = validated_currency_code(currency)
    if confirmed_currency is None:
        raise ValueError("observer incident currency must be confirmed")
    currency = confirmed_currency

    async with engine.begin() as conn:
        await _lock_and_assert_cabinet_fence(conn, cabinet_lease)
        policy_now = (await conn.execute(text("SELECT NOW()"))).scalar_one()
        context = (
            await conn.execute(
                text(
                    """
                    SELECT a.fb_ad_id, a.ad_name, c.ad_account_id,
                           s.open_state_token AS previous_open_token,
                           s.alert_state AS previous_state,
                           COALESCE(s.snoozed_until > NOW(), FALSE) AS is_snoozed
                    FROM fb_ads a
                    JOIN fb_adsets aset ON aset.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = aset.campaign_id
                    LEFT JOIN ad_alert_state s ON s.ad_id = a.id
                    WHERE a.id = :ad_id
                    LIMIT 1
                    """
                ),
                {"ad_id": ad_id},
            )
        ).first()
        effective_fb_ad_id = fb_ad_id or (str(context.fb_ad_id) if context else None)
        effective_account_id = ad_account_id or (
            str(context.ad_account_id) if context and context.ad_account_id else None
        )

        # 1. upsert ad_alert_state с защитой от регресса claimed/disabled
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
                    -- H1/MID-2: сбрасываем snooze при закрытии инцидента (→normal) И при
                    -- СТАРТЕ нового инцидента из normal (normal→warning_sent/stop_sent).
                    -- Иначе устаревший snoozed_until (закрытого инцидента или ошибочно
                    -- поставленный на normal-ад) подавил бы НОВЫЙ STOP до истечения окна —
                    -- money-дыра (убыточный ад крутится без стопа ~2ч). Снуз действует
                    -- только внутри инцидента, для которого его поставили.
                    snoozed_until = CASE
                        WHEN EXCLUDED.alert_state = 'normal' THEN NULL
                        WHEN ad_alert_state.alert_state = 'normal' THEN NULL
                        ELSE ad_alert_state.snoozed_until
                    END,
                    last_transition_at = CASE
                        WHEN ad_alert_state.alert_state != EXCLUDED.alert_state
                        THEN NOW() ELSE ad_alert_state.last_transition_at
                    END
                WHERE ad_alert_state.alert_state NOT IN ('claimed', 'disabled')
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

        serialized_metrics = {
            **_serialize_metrics(metrics_snapshot),
            "currency": currency,
        }

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
                    "m": json.dumps(serialized_metrics),
                    "tok": transition.new_open_token,
                    "scan_id": scan_id,
                },
            )

        incident_id: uuid.UUID | None = None
        incident_correlation_id = uuid.uuid4()
        open_token = transition.new_open_token or (context.previous_open_token if context else None)
        if transition.emit_alert and transition.alert_stage is not None and open_token is not None:
            if not effective_fb_ad_id:
                raise RuntimeError("cannot create incident without fb_ad_id")
            severity = "critical" if transition.alert_stage == "stop" else "warning"
            ad_name = str(context.ad_name) if context and context.ad_name else effective_fb_ad_id
            title = f"{ad_name} · {'STOP' if severity == 'critical' else 'WARNING'}"
            summary = _incident_summary(
                serialized_metrics,
                transition.alert_rule_codes,
                currency=currency,
            )
            incident_key = f"ad:{effective_fb_ad_id}:{open_token}"
            facts = {
                "fb_ad_id": effective_fb_ad_id,
                "ad_name": ad_name,
                "stage": transition.alert_stage,
                "rule_codes": list(transition.alert_rule_codes),
                "currency": currency,
                "currency_state": "confirmed",
                "metrics": serialized_metrics,
            }
            risk_ratio = _risk_ratio_from_metrics(
                serialized_metrics,
                stage=transition.alert_stage,
            )
            if risk_ratio is not None:
                facts["risk_ratio"] = str(risk_ratio)
            incident_row = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO incidents
                            (incident_key, generation, resource_type, resource_id,
                             ad_account_id, severity, status, title, summary,
                             facts, correlation_id, opened_at)
                        VALUES
                            (:incident_key, 1, 'ad', :resource_id, :account_id,
                             :severity, 'open', :title, :summary,
                             CAST(:facts AS JSONB), :correlation_id, NOW())
                        ON CONFLICT (incident_key)
                          WHERE status IN ('open','acknowledged','executing')
                        DO UPDATE SET severity = EXCLUDED.severity,
                                      status = 'open',
                                      title = EXCLUDED.title,
                                      summary = EXCLUDED.summary,
                                      facts = EXCLUDED.facts,
                                      updated_at = NOW()
                        RETURNING id, correlation_id
                        """
                    ),
                    {
                        "incident_key": incident_key,
                        "resource_id": effective_fb_ad_id,
                        "account_id": effective_account_id,
                        "severity": severity,
                        "title": title[:200],
                        "summary": summary[:700],
                        "facts": json.dumps(facts, ensure_ascii=False),
                        "correlation_id": incident_correlation_id,
                    },
                )
            ).one()
            incident_id = uuid.UUID(str(incident_row.id))
            incident_correlation_id = uuid.UUID(str(incident_row.correlation_id))
            lines = _incident_lines(serialized_metrics, currency=currency)
            await enqueue_notification_in_transaction(
                conn,
                NotificationEventSpec(
                    event_type=f"incident_{transition.alert_stage}",
                    severity=severity,
                    audience="owners",
                    facts=NotificationCardFacts(
                        title=title,
                        summary=summary,
                        lines=lines,
                        risk=_incident_risk(transition.alert_rule_codes),
                        open_target={"kind": "incident", "target_id": str(incident_id)},
                    ),
                    actions=[
                        NotificationActionSpec(
                            key="pause",
                            label="Отключить",
                            kind="pause_ad",
                            target_type="fb_ad",
                            target_id=effective_fb_ad_id,
                            target_payload={"incident_id": str(incident_id)},
                            required_role="owner",
                        )
                    ],
                    dedupe_key=(f"observer:{open_token}:{transition.alert_stage}"),
                    incident_id=incident_id,
                    correlation_id=incident_correlation_id,
                    scheduled_at=(
                        policy_now + timedelta(minutes=5) if severity == "warning" else None
                    ),
                ),
            )

        # A repeated warning remains quiet unless the canonical risk multiple
        # (max value/threshold across warning hits) grew by at least 25% since
        # the last emitted snapshot.  The row lock makes the threshold a CAS:
        # concurrent scans can create at most one snapshot for a given growth.
        if (
            not transition.emit_alert
            and transition.new_state == "warning_sent"
            and open_token is not None
            and effective_fb_ad_id
            and context is not None
            and str(context.previous_state or "") == "warning_sent"
            and not bool(context.is_snoozed)
        ):
            current_ratio = _risk_ratio_from_metrics(serialized_metrics, stage="warning")
            current_rule_codes = _rule_codes_from_metrics(
                serialized_metrics,
                stage="warning",
            )
            active_incident = (
                await conn.execute(
                    text(
                        """
                        SELECT id, generation, title, facts, correlation_id, opened_at
                        FROM incidents
                        WHERE incident_key = :incident_key
                          AND status IN ('open','acknowledged')
                        FOR UPDATE
                        """
                    ),
                    {"incident_key": f"ad:{effective_fb_ad_id}:{open_token}"},
                )
            ).first()
            if active_incident is not None and current_ratio is not None:
                previous_facts = dict(active_incident.facts or {})
                previous_ratio = _risk_ratio_from_incident_facts(previous_facts)
                if previous_ratio is None:
                    raise RuntimeError(
                        "active warning incident is missing canonical risk_ratio: "
                        f"{active_incident.id}"
                    )
                if _risk_growth_reached(previous_ratio, current_ratio):
                    incident_id = uuid.UUID(str(active_incident.id))
                    incident_correlation_id = uuid.UUID(str(active_incident.correlation_id))
                    ad_name = str(previous_facts.get("ad_name") or effective_fb_ad_id)
                    summary = _incident_summary(
                        serialized_metrics,
                        current_rule_codes,
                        currency=currency,
                    )
                    refreshed_facts = {
                        **previous_facts,
                        "fb_ad_id": effective_fb_ad_id,
                        "ad_name": ad_name,
                        "stage": "warning",
                        "rule_codes": list(current_rule_codes),
                        "currency": currency,
                        "currency_state": "confirmed",
                        "metrics": serialized_metrics,
                        "risk_ratio": str(current_ratio),
                    }
                    await conn.execute(
                        text(
                            """
                            UPDATE incidents
                            SET summary = :summary,
                                facts = CAST(:facts AS JSONB),
                                updated_at = NOW()
                            WHERE id = :incident_id
                            """
                        ),
                        {
                            "incident_id": incident_id,
                            "summary": summary[:700],
                            "facts": json.dumps(refreshed_facts, ensure_ascii=False),
                        },
                    )
                    aggregation_deadline = active_incident.opened_at + timedelta(minutes=5)
                    scheduled_at = max(policy_now, aggregation_deadline)
                    risk_marker = int(current_ratio * Decimal("10000"))
                    await enqueue_notification_in_transaction(
                        conn,
                        NotificationEventSpec(
                            event_type="incident_warning_growth",
                            severity="warning",
                            audience="owners",
                            facts=NotificationCardFacts(
                                title=str(active_incident.title),
                                summary=summary,
                                lines=_incident_lines(
                                    serialized_metrics,
                                    currency=currency,
                                ),
                                risk=_incident_risk(current_rule_codes),
                                status="Риск вырос ≥25%",
                                open_target={
                                    "kind": "incident",
                                    "target_id": str(incident_id),
                                },
                            ),
                            actions=[
                                NotificationActionSpec(
                                    key="pause",
                                    label="Отключить",
                                    kind="pause_ad",
                                    target_type="fb_ad",
                                    target_id=effective_fb_ad_id,
                                    target_payload={"incident_id": str(incident_id)},
                                    required_role="owner",
                                )
                            ],
                            dedupe_key=(f"observer:{open_token}:warning:risk:{risk_marker}"),
                            incident_id=incident_id,
                            correlation_id=incident_correlation_id,
                            scheduled_at=scheduled_at,
                        ),
                    )

        # Recovery is a first-class incident transition, not a silent message
        # disappearance. It bypasses quiet hours downstream.
        previous_token = context.previous_open_token if context else None
        if transition.new_state == "normal" and previous_token is not None:
            recovered = (
                await conn.execute(
                    text(
                        """
                        UPDATE incidents
                        SET status = 'resolved', resolved_at = NOW(), updated_at = NOW()
                        WHERE incident_key = :incident_key
                          AND status IN ('open','acknowledged','executing')
                        RETURNING id, title, correlation_id
                        """
                    ),
                    {"incident_key": f"ad:{effective_fb_ad_id}:{previous_token}"},
                )
            ).first()
            if recovered is not None:
                await enqueue_notification_in_transaction(
                    conn,
                    NotificationEventSpec(
                        event_type="incident_recovered",
                        severity="ok",
                        audience="owners",
                        facts=NotificationCardFacts(
                            title=str(recovered.title),
                            summary="Риск больше не подтверждается актуальным снимком.",
                            status="Восстановлено",
                        ),
                        dedupe_key=f"incident:{recovered.id}:resolved",
                        incident_id=uuid.UUID(str(recovered.id)),
                        correlation_id=uuid.UUID(str(recovered.correlation_id)),
                    ),
                )

        task_id: int | None = None
        if transition.create_disable_task:
            if not effective_fb_ad_id:
                raise RuntimeError("cannot create auto-pause task without fb_ad_id")
            token = open_token or uuid.uuid4()
            receipt = await CommandService(engine).enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=effective_fb_ad_id,
                requested_by="bot_auto_stop",
                idempotency_key=f"auto:pause_ad:{effective_fb_ad_id}:{token}",
                correlation_id=incident_correlation_id,
                max_attempts=_AUTO_STOP_MAX_ATTEMPTS,
                connection=conn,
            )
            task_id = receipt.task_id
        return task_id


# Кулдаун reopen: ад считаем «реально реактивированным» только если он в disabled
# дольше этого порога. Meta обновляет effective_status после pause с лагом (минуты) —
# свежевыключенный ад ещё показывает delivery=ACTIVE. Без кулдауна reopen ошибочно
# отменял бы только что сделанный auto-stop (регресс test_does_not_overwrite_disabled).
REACTIVATION_COOLDOWN_MINUTES = 15


async def reopen_reactivated_alert_state(
    engine: AsyncEngine,
    *,
    ad_id: uuid.UUID,
    cooldown_minutes: int = REACTIVATION_COOLDOWN_MINUTES,
    cabinet_lease: CabinetLease | None = None,
) -> bool:
    """Сброс disabled→normal для РЕАЛЬНО реактивированного ада (H3, observer-driven reopen).

    apply_fsm_transition защищает `disabled` WHERE-guard'ом (observer не затирает
    терминальные). Но если ад снова ACTIVE в кабинете (реактивирован вручную в Ads
    Manager — мимо подтверждённого enable-пути), FSM застревает в disabled
    и повторный STOP не сработает (убыточный ад крутится). Это явный reopen: возвращаем
    в normal + обнуляем snooze/token/stage, чтобы следующий decide() стартовал чистый
    инцидент.

    Time-guard: трогаем только ады, бывшие в disabled дольше cooldown_minutes — иначе
    отменили бы свежий auto-stop из-за лага Meta effective_status. Идемпотентно:
    rowcount=0 если ад не в disabled или выключен недавно.
    """
    async with engine.begin() as conn:
        await _lock_and_assert_cabinet_fence(conn, cabinet_lease)
        res = await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'normal',
                    current_stage = NULL,
                    open_state_token = NULL,
                    snoozed_until = NULL,
                    last_transition_at = NOW()
                WHERE ad_id = :aid
                  AND alert_state = 'disabled'
                  AND last_transition_at < NOW() - make_interval(mins => :cd)
                """
            ),
            {"aid": ad_id, "cd": cooldown_minutes},
        )
        return (res.rowcount or 0) > 0


async def mark_disabled_when_offline(
    engine: AsyncEngine,
    *,
    ad_id: uuid.UUID,
    cooldown_minutes: int = REACTIVATION_COOLDOWN_MINUTES,
    cabinet_lease: CabinetLease | None = None,
) -> bool:
    """Sync warning_sent/stop_sent → disabled для ада, который в Meta уже OFF (зеркало reopen).

    Терминальный `disabled` штатно ставит fsm_sync после УСПЕШНОЙ pause-мутации. Если
    pause упала (или ад выключили вручную/выше), FSM застревает в инциденте, хотя ад
    фактически OFF — рассинхрон навсегда (у OFF-ада нет метрик → нет переходов).

    Time-guard (cooldown_minutes): трогаем только ады, зависшие в инциденте дольше
    кулдауна — иначе опередили бы штатный fsm_sync на свежем stop_sent (наша pause
    ещё в полёте/только сработала, лаг Meta effective_status). Идемпотентно:
    rowcount=0 если ад не в warning_sent/stop_sent или инцидент свежий.
    """
    async with engine.begin() as conn:
        await _lock_and_assert_cabinet_fence(conn, cabinet_lease)
        row = (
            await conn.execute(
                text(
                    """
                UPDATE ad_alert_state AS state
                SET alert_state = 'disabled',
                    last_transition_at = NOW(),
                    updated_at = NOW()
                FROM fb_ads AS ad
                WHERE state.ad_id = :aid
                  AND ad.id = state.ad_id
                  AND state.alert_state IN ('warning_sent', 'stop_sent')
                  AND state.last_transition_at < NOW() - make_interval(mins => :cd)
                RETURNING ad.fb_ad_id, state.open_state_token
                """
                ),
                {"aid": ad_id, "cd": cooldown_minutes},
            )
        ).first()
        if row is None:
            return False
        fb_ad_id = str(row.fb_ad_id)
        if row.open_state_token is None:
            raise RuntimeError("offline incident transition has no open_state_token")
        open_state_token = uuid.UUID(str(row.open_state_token))
        await resolve_recurring_incident_in_transaction(
            conn,
            incident_key=f"ad:{fb_ad_id}:{open_state_token}",
            audience="owners",
            summary=f"Объявление {fb_ad_id} подтверждено OFF.",
        )
        await resolve_recurring_incident_in_transaction(
            conn,
            incident_key=f"{UNDELIVERED_INCIDENT_KEY_PREFIX}{fb_ad_id}",
            audience="owners",
            summary=f"Объявление {fb_ad_id} подтверждено OFF.",
        )
        await notify_owners_in_transaction(
            conn,
            event_type="sync_disabled",
            severity="ok",
            title="Объявление уже отключено",
            summary=f"{fb_ad_id} подтверждено OFF в Meta",
            status="confirmed",
            dedupe_key=f"sync_offline_disabled:{fb_ad_id}:{open_state_token}",
        )
        return True


def _warnings_from(transition: FsmTransition) -> tuple[str, ...]:
    return transition.alert_rule_codes if transition.alert_stage == "warning" else ()


def _stops_from(transition: FsmTransition) -> tuple[str, ...]:
    return transition.alert_rule_codes if transition.alert_stage == "stop" else ()


def _rule_codes_from_metrics(
    metrics: dict[str, Any],
    *,
    stage: str,
) -> tuple[str, ...]:
    codes: list[str] = []
    for hit in metrics.get("_hits") or []:
        if not isinstance(hit, dict) or str(hit.get("stage") or "").lower() != stage.lower():
            continue
        code = str(hit.get("code") or "").strip()
        if code and code not in codes:
            codes.append(code)
    return tuple(codes)


def _risk_ratio_from_metrics(
    metrics: dict[str, Any],
    *,
    stage: str,
) -> Decimal | None:
    """Return a dimensionless max(value/threshold) for comparable risk growth."""
    ratios: list[Decimal] = []
    for hit in metrics.get("_hits") or []:
        if not isinstance(hit, dict) or str(hit.get("stage") or "").lower() != stage.lower():
            continue
        try:
            value = Decimal(str(hit.get("value")))
            threshold = Decimal(str(hit.get("threshold")))
        except (ArithmeticError, TypeError, ValueError):
            continue
        if not value.is_finite() or not threshold.is_finite() or threshold <= 0:
            continue
        ratios.append(value / threshold)
    return max(ratios) if ratios else None


def _risk_ratio_from_incident_facts(facts: dict[str, Any]) -> Decimal | None:
    raw = facts.get("risk_ratio")
    if raw is not None:
        try:
            ratio = Decimal(str(raw))
        except (ArithmeticError, TypeError, ValueError):
            ratio = None
        if ratio is not None and ratio.is_finite() and ratio >= 0:
            return ratio
    metrics = facts.get("metrics")
    if isinstance(metrics, dict):
        return _risk_ratio_from_metrics(metrics, stage="warning")
    return None


def _risk_growth_reached(previous: Decimal | None, current: Decimal) -> bool:
    if previous is None or previous <= 0 or not current.is_finite():
        return False
    return current >= previous * _WARNING_RISK_GROWTH_FACTOR


def _decimal_text(value: object) -> str | None:
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    if not amount.is_finite():
        return None
    return format(amount, "f")


def _incident_summary(
    metrics: dict[str, Any],
    rule_codes: tuple[str, ...],
    *,
    currency: str | None,
) -> str:
    hits = metrics.get("_hits") or []
    first = next(
        (hit for hit in hits if isinstance(hit, dict) and hit.get("code") in rule_codes),
        None,
    )
    if first:
        code = str(first.get("code") or "risk").upper()
        rule_code = str(first.get("code") or "")
        value = _decimal_text(first.get("value")) or "?"
        threshold = _decimal_text(first.get("threshold")) or "?"
        if rule_code in _MONEY_RULE_CODES:
            if currency is None:
                return f"{code}: денежные значения не подтверждены"
            value = f"{value} {currency}"
            threshold = f"{threshold} {currency}"
        return f"{code}: {value} при пороге {threshold}"
    return " · ".join(code.upper() for code in rule_codes[:3]) or "Порог риска превышен"


def _incident_lines(
    metrics: dict[str, Any],
    *,
    currency: str | None,
) -> list[str]:
    parts: list[str] = []
    spend = metrics.get("spend")
    clicks = metrics.get("clicks")
    registrations = metrics.get("registrations")
    deposits = metrics.get("deposits")
    if spend is not None and currency is not None:
        amount = _decimal_text(spend)
        if amount is not None:
            parts.append(f"Spend {amount} {currency}")
    elif spend is not None:
        parts.append("Spend не показан: валюта не подтверждена")
    if clicks is not None:
        parts.append(f"{clicks} кликов")
    if registrations is not None:
        parts.append(f"{registrations} рег")
    if deposits is not None:
        parts.append(f"{deposits} депозитов")
    return [" · ".join(parts)] if parts else []


def _incident_risk(rule_codes: tuple[str, ...]) -> str:
    codes = set(rule_codes)
    if "spend_no_dep_range" in codes:
        return "расход без первого депозита"
    if "spend_with_dep_range" in codes:
        return "расход выше порога после депозита"
    if any("cpl" in code for code in codes):
        return "стоимость лида выше допустимой"
    if any("cpc" in code for code in codes):
        return "стоимость клика выше допустимой"
    return "метрики вышли за подтверждённый порог"


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
    correlation_id: uuid.UUID | None = None,
) -> int | None:
    """Если FSM решил создать stop-задачу — auto-stop через Marketing API (pause_ad).

    Канал исполнения — только Marketing API: task_type='meta_api_mutation' (pause_ad)
    → meta_api_worker → точно по ad_id. Detect остаётся через am_tabular (graph-канал);
    DOM-канал отключения (toggle-клик) удалён.

    idempotency_key привязан к open_token инцидента — гарантирует одну задачу на
    инцидент (повторный STOP того же incident'а → UNIQUE conflict → no-op).

    CommandService берёт явный кабинет из канонического каталога, чтобы UI,
    Telegram и observer строили идентичный payload.
    """
    if not transition.create_disable_task:
        return None
    token = open_token or transition.new_open_token or uuid.uuid4()
    receipt = await CommandService(engine).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id=fb_ad_id,
        requested_by="bot_auto_stop",
        idempotency_key=f"auto:pause_ad:{fb_ad_id}:{token}",
        correlation_id=correlation_id,
        max_attempts=_AUTO_STOP_MAX_ATTEMPTS,
    )
    return receipt.task_id


# Авто-стоп — money-критичный: при outage graph.facebook.com (Vision-канал
# периодически «Failed to fetch») дефолтных 5 попыток (~7.5 мин) не хватает.
# N=15 даёт ровно ~1 час: 3 экспоненциальных интервала (60+120+240=420с) +
# 11×300с-кэп = 3720с ≈ 62 мин. Этого достаточно для transient outage.
# Почему не 6ч (было 72): мёртвый канал детектирует health_watchdog probe
# (MetaApiClient.check_health full_probe) + CRITICAL-алерт в TG — дальше
# circuit-breaker открывается и масштаб проблемы виден. 6 часов молчаливого
# битья в стену только маскировали корень; 1 час — разумный компромисс между
# transient-устойчивостью и быстрой эскалацией к человеку.
_AUTO_STOP_MAX_ATTEMPTS = 15


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


async def mark_alert_state_claimed(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
) -> bool:
    """Перевести ad_alert_state в 'claimed' после ручного dis (юзер взял управление).

    L2: без этого observer при продолжающихся STOP-метриках плодит параллельную
    auto-pause задачу (идемпотентную, но шумную в очереди/аудите). 'claimed'
    помечает, что человек уже обработал инцидент.

    Идемпотентно: UPDATE только из активных alert-состояний (warning_sent, stop_sent).
    Из 'normal' НЕ переводим — иначе ад залип бы в claimed без инцидента, а
    observer-reopen (reopen_reactivated_alert_state, H3) покрывает только 'disabled'
    → ад стал бы money-blind. Из 'claimed'/'disabled' — no-op (уже обработан).

    Returns: True если строку обновили, False если no-op (или ad не найден).
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'claimed',
                    last_transition_at = NOW(),
                    updated_at = NOW()
                WHERE ad_id = (SELECT id FROM fb_ads WHERE fb_ad_id = :fbid)
                  AND alert_state IN ('warning_sent', 'stop_sent')
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
