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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.state_machine import FsmTransition

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
    delivery_status: str | None = None,
    ad_account_id: str | None = None,
    creative_thumb_url: str | None = None,
    creative_image_url: str | None = None,
    adset_pixel_id: str | None = None,
    adset_daily_budget: str | None = None,
    adset_lifetime_budget: str | None = None,
    adset_budget_remaining: str | None = None,
    adset_learning_stage: str | None = None,
) -> uuid.UUID:
    """UPSERT offer → campaign → adset → ad, возвращает fb_ads.id (UUID).

    Все три уровня каталога обновляют last_seen_at — это позволяет отличить «живые»
    объявления от исторических.

    delivery_status — текущий статус доставки ad'а из скана; пустую строку
    нормализуем в NULL (фронту не нужен "" вместо реального статуса).

    ad_account_id — кабинет, из которого пришла строка скана (мульти-кабинет).
    None — не трогаем существующее значение (COALESCE), чтобы fallback-сканы
    без кабинета не затирали уже известную привязку.

    creative_* / adset_* (Волна 1) — превью крео + метаданные адсета. Пустые
    нормализуем в NULL и COALESCE'им: скан без значения не затирает уже известное
    (URL крео обновляется только когда Graph его отдал).
    """
    # Пустой/пробельный статус → NULL: пишем только осмысленное значение.
    delivery_status = delivery_status.strip() if delivery_status else None
    delivery_status = delivery_status or None
    # Пустой кабинет → NULL (не затираем существующий COALESCE'ом ниже).
    ad_account_id = (ad_account_id or "").strip() or None

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
        # --- campaign: идентичность = fb_campaign_id (HIGH-3, миграция 0020) ---
        # Одноимённые кампании РАЗНЫХ кабинетов — разные строки; upsert по имени
        # сливал их в одну (ads обоих кабинетов цеплялись к ней, ad_account_id прыгал).
        params = {
            "fbcid": fb_campaign_id,
            "cname": campaign_name,
            "oid": offer_id,
            "now": now,
            "acct": ad_account_id,
        }
        if fb_campaign_id:
            # Adoption: legacy-строка с тем же именем без Graph ID (старые DOM-сканы)
            # получает ID, чтобы ON CONFLICT ниже попал в неё, а не создал дубль.
            # Guard NOT EXISTS — не красть ID у строки, когда он уже занят другой кампанией.
            await conn.execute(
                text(
                    """
                    UPDATE fb_campaigns SET fb_campaign_id = :fbcid
                    WHERE campaign_name = :cname
                      AND fb_campaign_id IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM fb_campaigns WHERE fb_campaign_id = :fbcid
                      )
                    """
                ),
                params,
            )
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
                            ad_account_id =
                                COALESCE(EXCLUDED.ad_account_id, fb_campaigns.ad_account_id),
                            is_active = TRUE
                        RETURNING id
                        """
                    ),
                    params,
                )
            ).first()
        else:
            # Fallback без Graph ID (исторический путь): матчим по имени, предпочитая
            # свой кабинет. Только observer пишет каталог (последовательно) — race
            # SELECT→INSERT здесь не возникает.
            # Adoption-guard: матчим ТОЛЬКО свободные legacy-строки (fb_campaign_id IS
            # NULL) — безымянный скан не должен красть/сливаться с уже идентифицированной
            # кампанией (иначе одноимённые кампании разных кабинетов путаются).
            cmp_row = (
                await conn.execute(
                    text(
                        """
                        SELECT id FROM fb_campaigns
                        WHERE campaign_name = :cname
                          AND fb_campaign_id IS NULL
                        ORDER BY (ad_account_id IS NOT DISTINCT FROM :acct) DESC, created_at
                        LIMIT 1
                        """
                    ),
                    params,
                )
            ).first()
            if cmp_row is not None:
                await conn.execute(
                    text(
                        """
                        UPDATE fb_campaigns
                        SET last_seen_at = :now,
                            offer_id = COALESCE(:oid, offer_id),
                            ad_account_id = COALESCE(:acct, ad_account_id),
                            is_active = TRUE
                        WHERE id = :cid
                        """
                    ),
                    {**params, "cid": cmp_row[0]},
                )
            else:
                cmp_row = (
                    await conn.execute(
                        text(
                            """
                            INSERT INTO fb_campaigns
                                (fb_campaign_id, campaign_name, offer_id, last_seen_at, ad_account_id)
                            VALUES (NULL, :cname, :oid, :now, :acct)
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
                    ON CONFLICT (campaign_id, adset_name) DO UPDATE
                    SET last_seen_at = :now,
                        fb_adset_id = COALESCE(EXCLUDED.fb_adset_id, fb_adsets.fb_adset_id),
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

    WHERE-guard в DO UPDATE: observer НЕ затирает терминальные состояния
    (claimed / disabled). Эти состояния выставляет либо telegram_poller (юзер
    кликнул «Отключить»), либо toggle_executor (после успешного disable). Их
    обратный сброс в normal/warning_sent/stop_sent делается отдельными
    функциями reset_alert_state_after_*_succeeded + observer-reopen логикой.
    """
    async with engine.begin() as conn:
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
                    -- H1: incident закрыт (→normal) → сбрасываем snooze. Иначе устаревший
                    -- snoozed_until от закрытого инцидента подавил бы НОВЫЙ STOP (money-дыра:
                    -- убыточный ад крутится без стопа до истечения старого снуза, ~2ч).
                    snoozed_until = CASE
                        WHEN EXCLUDED.alert_state = 'normal' THEN NULL
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
) -> bool:
    """Сброс disabled→normal для РЕАЛЬНО реактивированного ада (H3, observer-driven reopen).

    apply_fsm_transition защищает `disabled` WHERE-guard'ом (observer не затирает
    терминальные). Но если ад снова ACTIVE в кабинете (реактивирован вручную в Ads
    Manager или autostart bulk-activate — МИМО enable-пути), FSM застревает в disabled
    и повторный STOP не сработает (убыточный ад крутится). Это явный reopen: возвращаем
    в normal + обнуляем snooze/token/stage, чтобы следующий decide() стартовал чистый
    инцидент.

    Time-guard: трогаем только ады, бывшие в disabled дольше cooldown_minutes — иначе
    отменили бы свежий auto-stop из-за лага Meta effective_status. Идемпотентно:
    rowcount=0 если ад не в disabled или выключен недавно.
    """
    async with engine.begin() as conn:
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
        res = await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'disabled',
                    last_transition_at = NOW(),
                    updated_at = NOW()
                WHERE ad_id = :aid
                  AND alert_state IN ('warning_sent', 'stop_sent')
                  AND last_transition_at < NOW() - make_interval(mins => :cd)
                """
            ),
            {"aid": ad_id, "cd": cooldown_minutes},
        )
        return (res.rowcount or 0) > 0


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
    ad_account_id: str | None = None,
) -> int | None:
    """Если FSM решил создать stop-задачу — auto-stop через Marketing API (pause_ad).

    Канал исполнения — только Marketing API: task_type='meta_api_mutation' (pause_ad)
    → meta_api_worker → точно по ad_id. Detect остаётся через am_tabular (graph-канал);
    DOM-канал отключения (toggle-клик) удалён.

    idempotency_key привязан к open_token инцидента — гарантирует одну задачу на
    инцидент (повторный STOP того же incident'а → UNIQUE conflict → no-op).

    ad_account_id — мульти-кабинет: кабинет текущего скана; mutation исполнится
    из вкладки этого кабинета. None — legacy primary-вкладка.
    """
    if not transition.create_disable_task:
        return None
    token = open_token or transition.new_open_token or uuid.uuid4()
    return await _create_pause_mutation(
        engine, fb_ad_id=fb_ad_id, token=token, ad_account_id=ad_account_id
    )


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


async def _create_pause_mutation(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
    token: uuid.UUID,
    ad_account_id: str | None = None,
) -> int | None:
    """Создать meta_api_mutation pause_ad для авто-стопа.

    target_id = fb_ad_id (числовой Graph ID). idempotency_key привязан к token
    инцидента. status='pending' (исполняется meta_api_worker'ом сразу, без
    draft-подтверждения — это автоматический стоп бота).

    max_attempts повышен (_AUTO_STOP_MAX_ATTEMPTS) против дефолтных 5 — чтобы
    money-стоп пережил длинный сетевой outage graph.facebook.com.
    """
    from core.meta_api.queue import create_mutation_task
    from core.meta_api.schemas import MetaMutationPayload

    payload = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id=fb_ad_id,
        params={},
        ad_account_id=ad_account_id,
    )
    key = f"auto:pause_ad:{fb_ad_id}:{token}"
    return await create_mutation_task(
        engine,
        payload=payload,
        requested_by="bot_auto_stop",
        status="pending",
        idempotency_key=key,
        max_attempts=_AUTO_STOP_MAX_ATTEMPTS,
    )


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
