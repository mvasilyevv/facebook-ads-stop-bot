# -*- coding: utf-8 -*-
"""Главный pipeline observer: один scan-цикл (set of ScannedAdRow) → персист и FSM.

Минимальный путь, без эскалаторов хрупкости и adaptive CPA — baseline = cpa_threshold
из offer_rules. Расширения добавим отдельно.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.adset_pro.queries import (
    load_external_deposits_batch,
    load_external_registrations_batch,
)
from core.cabinet_day import is_cabinet_day_reset_scan
from core.meta_api.account_tz import (
    resolve_required_account_currency,
    resolve_required_cabinet_day,
)
from core.meta_api.identity import require_ad_account_id
from core.money import (
    InvalidCurrencyAmountError,
    currency_exponent,
    require_currency_exponent,
    validated_currency_code,
)
from core.observer.cabinet_supervisor import CabinetLease
from core.observer.enable_grace import (
    EnableGrace,
    clear_enable_grace_for_currency_mismatch,
    grace_is_active,
)
from core.observer.queries import (
    OfferRules,
    campaign_matches_owner,
    load_active_offers,
    load_alert_state_by_fb_ad_id,
    match_offer_for_ad,
)
from core.observer.state_machine import (
    FsmInput,
    decide,
    should_reopen_disabled,
    should_sync_disabled,
)
from core.observer.writers import (
    apply_fsm_transition,
    insert_metrics,
    mark_disabled_when_offline,
    reopen_reactivated_alert_state,
    upsert_catalog_hierarchy,
)
from core.rules.evaluator import evaluate_stop_rules
from core.rules.types import (
    CPC_PERCENT_OF_CPA,
    CPL_PERCENT_OF_CPA,
    CPR_PERCENT_OF_CPA,
    MIN_RATIO_DENOMINATOR,
    REGS_NO_DEP_STOP_COUNT,
    SPEND_NO_DEP_FROM_PERCENT,
    SPEND_NO_DEP_TO_PERCENT,
    SPEND_WITH_DEP_FROM_PERCENT,
    SPEND_WITH_DEP_TO_PERCENT,
    RuleContext,
    RuleEvaluation,
)
from core.scanner.models import ScannedAdRow

logger = logging.getLogger(__name__)


class MissingOfferCpaError(ValueError):
    """The matched offer has no safe monetary baseline for rule evaluation."""


class InvalidOfferSensitivityError(ValueError):
    """The matched offer has no safe, complete sensitivity configuration."""


class OfferCurrencyMismatchError(ValueError):
    """The offer rule currency is absent or differs from the Meta cabinet."""


async def _database_cycle_timestamp(engine: AsyncEngine) -> datetime:
    """Return the authoritative boundary for one observer cycle.

    Tracker projection timestamps are written by PostgreSQL. Using the Python
    host clock as the exclusive query boundary can therefore hide an already
    committed event when the database clock is even slightly ahead, producing
    a false no-deposit auto-stop. Keep explicit timestamps for deterministic
    callers, but source the production default from the same clock that owns
    the persisted evidence.
    """
    async with engine.connect() as conn:
        observed_at = (await conn.execute(text("SELECT clock_timestamp()"))).scalar_one()
    if getattr(observed_at, "tzinfo", None) is None:
        raise RuntimeError("PostgreSQL returned an invalid timezone-aware cycle timestamp")
    return observed_at.astimezone(timezone.utc)


def with_effective_tracker_registrations(
    row: ScannedAdRow,
    tracker_registrations: int,
) -> ScannedAdRow:
    """Use the larger confirmed source value without mutating or double-counting."""
    effective = max(int(row.registrations or 0), int(tracker_registrations or 0))
    return row if effective == row.registrations else replace(row, registrations=effective)


@dataclass
class CycleResult:
    """Итог одного scan-цикла."""

    scan_id: int | None = None
    rows_total: int = 0
    rows_with_offer: int = 0
    rows_without_offer: int = 0
    rows_foreign: int = 0  # отброшено owner-фильтром (чужие кампании без owner-тега)
    alerts_warning: int = 0
    alerts_stop: int = 0
    # Сколько объявлений ПОСЛЕ цикла сидят в открытом инциденте (не переходы, а состояние):
    # warning_sent → удержание ELEVATED; stop_sent/claimed (пауза не подтверждена, деньги
    # ещё капают) → удержание CRITICAL. Без этого адаптив ускорялся ровно на один цикл
    # после перехода и возвращался к базе, пока ад стоял у порога (инцидент 02.07).
    ads_in_warning_state: int = 0
    ads_in_stop_state: int = 0
    disable_tasks_created: int = 0
    # Кейс куратора: строки, где срабатывания правил подавлены активным enable-grace.
    rows_grace_suppressed: int = 0
    # Any row-level persistence/evaluation failure makes the cabinet snapshot
    # partial. It must never be reported fresh/healthy after silently skipping
    # a row that could have changed a money decision.
    row_errors: list[str] = field(default_factory=list)
    currency_mismatch_offers: list[str] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


def build_rule_context(
    offer: OfferRules,
    *,
    account_currency: str,
    currency_exponent: int,
    external_deposits: int = 0,
    frequency_current: Decimal | None = None,
    impressions: int | None = None,
    reach: int | None = None,
) -> RuleContext:
    """OfferRules → RuleContext с минимальным набором параметров.

    cpa_amount = cpa_threshold из offer_rules. NULL, non-finite и неположительное
    значение fail-closed: без подтверждённого monetary baseline правила нельзя
    ни вычислять, ни превращать в auto-pause.
    external_deposits — из AdSet.pro трекера, защищают от STOP при наличии депозита,
    которого Meta Ads Manager ещё не видит.

    frequency-anomaly (правило 7, #37) — opt-in per-offer через offer.frequency_threshold:
    NULL/0 → правило выключено для этого оффера; задан → stop-порог = frequency_threshold,
    warning — подтверждённый per-offer процент от stop (как у CPC/CPL/CPR).
    Только абсолютный порог, без истории роста за час (поле frequency_1h_ago удалено
    из RuleContext как мёртвое — см. core/rules/types.py).

    impressions/reach: кладём в RuleContext ВСЕГДА. Гейт-минимум по показам УБРАН
    (решение байера): guardrail (cpc/cpl/cpr при 0 событий) стопает сразу по порогу, не
    дожидаясь накопления показов — перекрут вреднее статистической нерепрезентативности.
    reach при этом — знаменатель частоты: ниже MIN_RATIO_DENOMINATOR отношение считается
    неизвестным (#204, core/rules/types.py), и правило молчит. От выброса частоты на
    старте (freq 50-100 при крошечном reach) дополнительно защищает frequency_outlier_cap.
    """
    cpa = offer.cpa_threshold
    if cpa is None or not cpa.is_finite() or cpa <= 0:
        raise MissingOfferCpaError(f"offer {offer.code!r} has no positive finite cpa_threshold")
    account_currency, currency_exponent = require_currency_exponent(
        account_currency,
        currency_exponent,
    )
    offer_currency = validated_currency_code(offer.currency)
    if offer_currency != account_currency:
        raise OfferCurrencyMismatchError(
            f"offer {offer.code!r} currency {offer.currency!r} "
            f"does not match cabinet {account_currency!r}"
        )
    warning_pct = offer.warning_percent_of_stop
    stop_pct = offer.stop_percent_of_rule
    if (
        warning_pct is None
        or stop_pct is None
        or not warning_pct.is_finite()
        or not stop_pct.is_finite()
        or warning_pct <= 0
        or warning_pct > 100
        or stop_pct <= 0
        or stop_pct > 100
    ):
        raise InvalidOfferSensitivityError(
            f"offer {offer.code!r} has no confirmed sensitivity configuration"
        )

    freq_threshold = offer.frequency_threshold
    freq_enabled = freq_threshold is not None and freq_threshold > 0
    if freq_enabled:
        freq_stop = Decimal(freq_threshold)
        freq_warning = (freq_stop * warning_pct / Decimal("100")).quantize(Decimal("0.01"))
    else:
        # Дефолты RuleContext (не используются при enabled=False, заданы для валидности).
        freq_stop = Decimal("3.5")
        freq_warning = Decimal("2.5")

    try:
        return RuleContext(
            currency=account_currency,
            currency_exponent=currency_exponent,
            cpa_amount=cpa,
            warning_percent_of_stop=warning_pct,
            stop_percent_of_base=stop_pct,
            external_deposits=external_deposits,
            frequency_anomaly_enabled=freq_enabled,
            frequency_current=frequency_current if freq_enabled else None,
            frequency_stop_threshold=freq_stop,
            frequency_warning_threshold=freq_warning,
            impressions=impressions,
            reach=reach,
            # Настраиваемые базовые пороги (#260): null → константа-умолчание
            cpc_percent_of_cpa=(
                offer.cpc_percent_of_cpa
                if offer.cpc_percent_of_cpa is not None
                else CPC_PERCENT_OF_CPA
            ),
            cpl_percent_of_cpa=(
                offer.cpl_percent_of_cpa
                if offer.cpl_percent_of_cpa is not None
                else CPL_PERCENT_OF_CPA
            ),
            cpr_percent_of_cpa=(
                offer.cpr_percent_of_cpa
                if offer.cpr_percent_of_cpa is not None
                else CPR_PERCENT_OF_CPA
            ),
            regs_no_dep_stop_count=(
                offer.regs_no_dep_stop_count
                if offer.regs_no_dep_stop_count is not None
                else REGS_NO_DEP_STOP_COUNT
            ),
            spend_no_dep_from_percent=(
                offer.spend_no_dep_from_percent
                if offer.spend_no_dep_from_percent is not None
                else SPEND_NO_DEP_FROM_PERCENT
            ),
            spend_no_dep_to_percent=(
                offer.spend_no_dep_to_percent
                if offer.spend_no_dep_to_percent is not None
                else SPEND_NO_DEP_TO_PERCENT
            ),
            spend_with_dep_from_percent=(
                offer.spend_with_dep_from_percent
                if offer.spend_with_dep_from_percent is not None
                else SPEND_WITH_DEP_FROM_PERCENT
            ),
            spend_with_dep_to_percent=(
                offer.spend_with_dep_to_percent
                if offer.spend_with_dep_to_percent is not None
                else SPEND_WITH_DEP_TO_PERCENT
            ),
            min_ratio_denominator=(
                offer.min_ratio_denominator
                if offer.min_ratio_denominator is not None
                else MIN_RATIO_DENOMINATOR
            ),
        )
    except InvalidCurrencyAmountError as exc:
        raise MissingOfferCpaError(
            f"offer {offer.code!r} has invalid CPA precision for {account_currency}"
        ) from exc


def _row_to_metrics_dict(row: ScannedAdRow) -> dict[str, Any]:
    """ScannedAdRow → плоский dict для insert_metrics + alert_events.metrics_json."""
    return {
        "spend": row.spend,
        "reach": row.reach,
        "impressions": row.impressions,
        "clicks": row.clicks,
        "cpc": row.cpc,
        "ctr": row.ctr,
        "cost_per_result": row.cost_per_result,
        "cpm": row.cpm,
        "frequency": row.frequency,
        "leads": row.leads,
        "cost_per_lead": row.cost_per_lead,
        "registrations": row.registrations,
        "cost_per_registration": row.cost_per_registration,
        "deposits": row.deposits,
        "outbound_clicks": row.outbound_clicks,
        "outbound_ctr": row.outbound_ctr,
        "landing_page_views": row.landing_page_views,
        "cost_per_landing_page_view": row.cost_per_landing_page_view,
    }


def _hits_payload(evaluation: RuleEvaluation) -> list[dict[str, str]]:
    """Сериализует сработавшие правила (value/threshold) для alert_events.metrics_json.

    Renderer берёт отсюда точные СВЁРНУТЫЕ пороги (cpa × проценты), которые нельзя
    восстановить из offer_rules. Кладётся ключом '_hits' в metrics_snapshot ТОЛЬКО для
    alert-события — в ad_metrics не попадает (insert_metrics отрабатывает до обогащения).
    """
    out: list[dict[str, str]] = []
    for hit in (*evaluation.stop_hits, *evaluation.warning_hits):
        stage = hit.stage.value if hasattr(hit.stage, "value") else str(hit.stage)
        out.append(
            {
                "code": hit.code,
                "stage": stage,
                "value": str(hit.value),
                "threshold": str(hit.threshold),
            }
        )
    return out


async def process_scan_rows(
    engine: AsyncEngine,
    *,
    rows: list[ScannedAdRow],
    scan_id: int | None = None,
    cycle_ts: datetime | None = None,
    owner_tag: str | None = None,
    ad_account_id: str,
    cabinet_lease: CabinetLease | None = None,
) -> CycleResult:
    """Один scan-цикл. Идемпотентен по (ad_id, cycle_ts) и (idempotency_key).

    Args:
        rows: список ScannedAdRow которые пришли от scanner gRPC.
        scan_id: монотонный счётчик (для аналитики и связи с alert_events).
        cycle_ts: общий timestamp цикла — используется в ad_metrics + alert_events.
                   Дефолт — PostgreSQL clock_timestamp(), чтобы граница и
                   persisted tracker evidence жили на одном clock source.
        owner_tag: owner-scoping. Если задан — строки кампаний без этого тега
                   полностью игнорируются (не пишем метрики, не оцениваем правила,
                   не дизейблим). NULL — фильтр выключен. Защита от работы с чужими
                   кампаниями в общем рекламном кабинете.
        ad_account_id: обязательный кабинет, из которого пришли строки;
                   пишется в fb_campaigns.ad_account_id.
        Cabinet-day timezone and durable enable-grace are resolved from
                   PostgreSQL. Missing/invalid IANA timezone raises before any
                   metric, FSM, incident or money-task write.

    Returns:
        CycleResult с метриками цикла.
    """
    if cycle_ts is None:
        cycle_ts = await _database_cycle_timestamp(engine)
    if cycle_ts.tzinfo is None:
        raise ValueError("cycle_ts must be timezone-aware")
    ad_account_id = require_ad_account_id(ad_account_id)
    cabinet_day = await resolve_required_cabinet_day(
        engine,
        account_id=ad_account_id,
        now=cycle_ts,
    )
    account_currency = await resolve_required_account_currency(
        engine,
        account_id=ad_account_id,
        now=cycle_ts,
    )
    account_currency_exponent = currency_exponent(account_currency)
    tracker_day_start = cabinet_day.starts_at

    result = CycleResult(scan_id=scan_id, rows_total=len(rows))

    if not rows:
        result.finished_at = datetime.now(timezone.utc)
        return result

    # 1. Загрузка активных офферов один раз (≪10 строк обычно)
    offers = await load_active_offers(engine)

    # 2. Загрузка текущих FSM-состояний batch'ом по fb_ad_id (1 SQL вместо N)
    fb_ids = [r.fb_ad_id for r in rows if r.fb_ad_id]
    states = await load_alert_state_by_fb_ad_id(engine, fb_ad_ids=fb_ids)

    # 3. Внешние депозиты от AdSet.pro batch'ом (закрывают gap attribution с Meta).
    external_deposits = await load_external_deposits_batch(
        engine,
        fb_ad_ids=fb_ids,
        window_start=tracker_day_start,
        window_end=cycle_ts,
    )
    external_registrations = await load_external_registrations_batch(
        engine,
        fb_ad_ids=fb_ids,
        window_start=tracker_day_start,
        window_end=cycle_ts,
    )

    # MID-1: zero-scan начала кабинетных суток — ВСЕ строки без ненулевых метрик
    # (Meta обнулила счётчики на границе дня). Флаг вычисляем один раз на весь цикл
    # и прокидываем в FSM: активный инцидент (stop_sent/warning_sent) НЕ деэскалируем
    # по нулевой строке, иначе потеряли бы stop на границе суток.
    is_cabinet_reset = is_cabinet_day_reset_scan(rows)

    # 4. Прогоняем каждую строку
    for row in rows:
        try:
            await _process_one_row(
                engine,
                row=row,
                offers=offers,
                states=states,
                external_deposits=external_deposits,
                external_registrations=external_registrations,
                scan_id=scan_id,
                cycle_ts=cycle_ts,
                result=result,
                owner_tag=owner_tag,
                ad_account_id=ad_account_id,
                account_currency=account_currency,
                account_currency_exponent=account_currency_exponent,
                is_cabinet_reset=is_cabinet_reset,
                cabinet_day_start=tracker_day_start,
                cabinet_lease=cabinet_lease,
            )
        except Exception as exc:
            logger.exception(
                "observer: ошибка обработки fb_ad_id=%s, продолжаю остальные",
                row.fb_ad_id,
            )
            result.row_errors.append(f"{row.fb_ad_id}:{type(exc).__name__}")

    result.finished_at = datetime.now(timezone.utc)
    return result


async def _process_one_row(
    engine: AsyncEngine,
    *,
    row: ScannedAdRow,
    offers: list[OfferRules],
    states: dict,
    external_deposits: dict[str, int],
    scan_id: int | None,
    cycle_ts: datetime,
    result: CycleResult,
    owner_tag: str | None = None,
    ad_account_id: str,
    account_currency: str,
    account_currency_exponent: int,
    is_cabinet_reset: bool = False,
    external_registrations: dict[str, int] | None = None,
    cabinet_day_start: datetime,
    cabinet_lease: CabinetLease | None = None,
) -> None:
    """Обработка одной строки. Вынесено отдельно ради читаемости + try/except в caller'е."""

    # Production actors pass the fence into every write transaction. Tests may
    # omit only the lease, never the cabinet identity.
    fence_kwargs = {"cabinet_lease": cabinet_lease} if cabinet_lease is not None else {}

    # Meta and AdSet.pro may report the same registration with different delay.
    # Keep the immutable scanner DTO and use the confirmed maximum, never a sum.
    tracker_registrations = (
        (external_registrations or {}).get(row.fb_ad_id, 0) if row.fb_ad_id else 0
    )
    row = with_effective_tracker_registrations(row, tracker_registrations)

    # --- Owner-scoping: чужие кампании (без owner-тега) полностью игнорируем ---
    # Не пишем метрики, не оцениваем правила, не создаём disable — бот их "не видит".
    if not campaign_matches_owner(
        campaign_name=row.campaign_name, ad_name=row.ad_name, owner_tag=owner_tag
    ):
        result.rows_foreign += 1
        return

    # --- Матчим оффер ---
    matched_offer = match_offer_for_ad(
        campaign_name=row.campaign_name,
        ad_name=row.ad_name,
        offers=offers,
    )

    if matched_offer is None:
        result.rows_without_offer += 1
        # Без оффера — не оцениваем правила, но всё равно сохраняем в каталог + метрики
        ad_id = await upsert_catalog_hierarchy(
            engine,
            fb_ad_id=row.fb_ad_id,
            ad_name=row.ad_name,
            fb_adset_id=row.adset_id,
            adset_name=row.adset_name,
            fb_campaign_id=row.campaign_id,
            campaign_name=row.campaign_name,
            offer_id=None,
            delivery_status=row.delivery_status,
            moderation_reason=row.moderation_reason,
            ad_account_id=ad_account_id,
            creative_thumb_url=row.creative_thumb_url,
            creative_image_url=row.creative_image_url,
            adset_pixel_id=row.adset_pixel_id,
            adset_daily_budget=row.adset_daily_budget,
            adset_lifetime_budget=row.adset_lifetime_budget,
            adset_budget_remaining=row.adset_budget_remaining,
            adset_learning_stage=row.adset_learning_stage,
            **fence_kwargs,
        )
        if await insert_metrics(
            engine,
            ad_id=ad_id,
            cycle_ts=cycle_ts,
            scan_id=scan_id,
            currency=account_currency,
            metrics=_row_to_metrics_dict(row),
            **fence_kwargs,
        ):
            return
        raise RuntimeError("ad_metrics_insert_failed")

    result.rows_with_offer += 1

    # Вычисляем durable rule-context тем же evaluator до catalog upsert, но
    # откладываем любую ошибку до записи scan metrics. Так сохраняется прежний
    # fail-closed порядок: ошибочная конфигурация не создаёт money-action, а сам
    # подтверждённый снимок остаётся наблюдаемым и не маскируется пропуском строки.
    ad_external_deposits = external_deposits.get(row.fb_ad_id, 0) if row.fb_ad_id else 0
    evaluation: RuleEvaluation | None = None
    evaluation_error: Exception | None = None
    try:
        ctx = build_rule_context(
            matched_offer,
            account_currency=account_currency,
            currency_exponent=account_currency_exponent,
            external_deposits=ad_external_deposits,
            frequency_current=row.frequency,
            impressions=row.impressions,
            reach=row.reach,
        )
        evaluation = evaluate_stop_rules(row, ctx)
    except Exception as exc:  # сохраняем прежний persist-before-fail порядок
        evaluation_error = exc
    nearest_stop = evaluation.nearest_stop if evaluation is not None else None
    nearest_stage = (
        nearest_stop.stage.value.lower()
        if nearest_stop is not None and nearest_stop.stage is not None
        else "none"
        if nearest_stop is not None
        else None
    )

    # --- Catalog upsert ---
    ad_id = await upsert_catalog_hierarchy(
        engine,
        fb_ad_id=row.fb_ad_id,
        ad_name=row.ad_name,
        fb_adset_id=row.adset_id,
        adset_name=row.adset_name,
        fb_campaign_id=row.campaign_id,
        campaign_name=row.campaign_name,
        offer_id=matched_offer.offer_id,
        delivery_status=row.delivery_status,
        moderation_reason=row.moderation_reason,
        ad_account_id=ad_account_id,
        creative_thumb_url=row.creative_thumb_url,
        creative_image_url=row.creative_image_url,
        adset_pixel_id=row.adset_pixel_id,
        adset_daily_budget=row.adset_daily_budget,
        adset_lifetime_budget=row.adset_lifetime_budget,
        adset_budget_remaining=row.adset_budget_remaining,
        adset_learning_stage=row.adset_learning_stage,
        nearest_rule_code=nearest_stop.code if nearest_stop is not None else None,
        nearest_rule_value=nearest_stop.value if nearest_stop is not None else None,
        nearest_rule_threshold=nearest_stop.threshold if nearest_stop is not None else None,
        nearest_rule_stage=nearest_stage,
        matched_offer_code=matched_offer.code if evaluation is not None else None,
        **fence_kwargs,
    )

    # --- Метрики (партиционированная таблица) ---
    metrics = _row_to_metrics_dict(row)
    metrics_inserted = await insert_metrics(
        engine,
        ad_id=ad_id,
        cycle_ts=cycle_ts,
        scan_id=scan_id,
        currency=account_currency,
        metrics=metrics,
        **fence_kwargs,
    )
    if not metrics_inserted:
        # Never evaluate rules or mutate the FSM from a snapshot that was not
        # durably recorded. Otherwise a partition/DB failure could create an
        # auto-pause while the scan itself appeared healthy and unauditable.
        raise RuntimeError("ad_metrics_insert_failed")

    if isinstance(evaluation_error, OfferCurrencyMismatchError):
        marker = f"{matched_offer.code}:{matched_offer.currency or 'unknown'}!={account_currency}"
        if marker not in result.currency_mismatch_offers:
            result.currency_mismatch_offers.append(marker)
        raise evaluation_error

    current = states.get(row.fb_ad_id)
    if (
        current is not None
        and current.enable_grace_until is not None
        and (
            current.enable_grace_currency != account_currency
            or current.enable_grace_currency_exponent != account_currency_exponent
        )
    ):
        await clear_enable_grace_for_currency_mismatch(
            engine,
            ad_id=current.ad_id,
            currency=account_currency,
            currency_exponent=account_currency_exponent,
            cabinet_lease=cabinet_lease,
        )
        current = replace(
            current,
            enable_grace_until=None,
            enable_grace_spend_cap=None,
            enable_grace_baseline_spend=None,
            enable_grace_cabinet_day_start=None,
            enable_grace_currency=None,
            enable_grace_currency_exponent=None,
        )

    if evaluation_error is not None:
        raise evaluation_error
    assert evaluation is not None

    # --- Оценка правил уже выполнена до upsert; здесь меняется только FSM ---
    stop_codes = tuple(evaluation.stop_rule_codes)
    warning_codes = tuple(evaluation.warning_rule_codes)

    # --- Grace «держать до цены лида» (кейс куратора) ---
    # Под активным grace правила «не видят» нарушений: подавляем И алерт, И авто-стоп
    # (в отличие от снуза, который по MID-2 глушит только алерты). Подавляем именно
    # коды ДО decide(): иначе FSM ушёл бы в stop_sent без задачи и после окна grace
    # повторный STOP уже не сработал бы (FSM однонаправленная). Выход из grace —
    # по времени ИЛИ по спенд-капу (~1×CPA) — дальше обычные правила.
    grace = None
    if (
        current is not None
        and current.enable_grace_until is not None
        and current.enable_grace_spend_cap is not None
        and current.enable_grace_baseline_spend is not None
        and current.enable_grace_cabinet_day_start is not None
        and current.enable_grace_currency is not None
        and current.enable_grace_currency_exponent is not None
    ):
        grace = EnableGrace(
            until=current.enable_grace_until,
            spend_cap=current.enable_grace_spend_cap,
            baseline_spend=current.enable_grace_baseline_spend,
            cabinet_day_start=current.enable_grace_cabinet_day_start,
            currency=current.enable_grace_currency,
            currency_exponent=current.enable_grace_currency_exponent,
        )
    if (
        grace is not None
        and (stop_codes or warning_codes)
        and grace_is_active(
            grace,
            now=cycle_ts,
            spend=row.spend,
            cabinet_day_start=cabinet_day_start,
            currency=account_currency,
            currency_exponent=account_currency_exponent,
        )
    ):
        logger.info(
            "observer: enable-grace активен fb_ad_id=%s (до %s, cap=%s, spend=%s) — "
            "срабатывания правил подавлены: %s",
            row.fb_ad_id,
            grace.until.isoformat(),
            grace.spend_cap,
            row.spend,
            list(stop_codes + warning_codes),
        )
        stop_codes, warning_codes = (), ()
        result.rows_grace_suppressed += 1

    # --- FSM ---
    # H3: реактивированный disabled-ад (снова ACTIVE в кабинете — мимо enable-пути) →
    # reopen в normal, иначе FSM застрянет в disabled и повторный STOP не сработает.
    if current and should_reopen_disabled(current.alert_state, row.delivery_status):
        # reopen срабатывает только если ад в disabled дольше кулдауна (защита от лага
        # Meta effective_status на свежем disable). True → реально сброшен в normal.
        if await reopen_reactivated_alert_state(
            engine,
            ad_id=ad_id,
            **fence_kwargs,
        ):
            logger.info(
                "observer: reopen disabled→normal (реактивирован ACTIVE) fb_ad_id=%s",
                row.fb_ad_id,
            )
            current = None  # для FsmInput трактуем как normal — стартует свежий инцидент
    elif current and should_sync_disabled(current.alert_state, row.delivery_status):
        # Зеркало reopen: ад завис в инциденте, но в Meta уже OFF (наша pause упала или
        # выключили вручную) → штатный fsm_sync не отработал. Приводим FSM к disabled.
        if await mark_disabled_when_offline(
            engine,
            ad_id=ad_id,
            **fence_kwargs,
        ):
            logger.info(
                "observer: sync %s→disabled (ад OFF, fsm_sync не отработал) fb_ad_id=%s",
                current.alert_state,
                row.fb_ad_id,
            )
            # Инцидент закрыт; метрики OFF-ада заморожены — FSM/disable-task дальше не гоняем.
            return
    fsm_input = FsmInput(
        current_state=current.alert_state if current else "normal",
        current_stage=current.current_stage if current else None,
        current_open_token=current.open_state_token if current else None,
        warning_rule_codes=warning_codes,
        stop_rule_codes=stop_codes,
        is_cabinet_reset=is_cabinet_reset,
    )
    transition = decide(fsm_input)

    # --- Snooze: пропускаем emit если ад заснужен ---
    if current and current.snoozed_until and current.snoozed_until > cycle_ts:
        # FSM-состояние всё равно обновим (state может deescalate), но не emit
        transition = _suppress_emit(transition, reason="snoozed")

    # --- Persist FSM + event ---
    # Детали сработавших правил (value/threshold) → в alert_events.metrics_json,
    # чтобы renderer показал точное значение и валюту без реконструкции свёрнутых
    # порогов. В ad_metrics не попадает — insert_metrics уже выполнен выше.
    hits_payload = _hits_payload(evaluation)
    metrics_for_event = {**metrics, "_hits": hits_payload} if hits_payload else metrics
    task_id = await apply_fsm_transition(
        engine,
        ad_id=ad_id,
        transition=transition,
        metrics_snapshot=metrics_for_event,
        scan_id=scan_id,
        fb_ad_id=row.fb_ad_id,
        ad_account_id=ad_account_id,
        currency=account_currency,
        **fence_kwargs,
    )

    if transition.emit_alert:
        if transition.alert_stage == "warning":
            result.alerts_warning += 1
        elif transition.alert_stage == "stop":
            result.alerts_stop += 1

    # Учёт стоячих инцидентов для адаптивного интервала. Снуз НЕ исключаем:
    # частота скана — про свежесть данных, а не про нотификации.
    _bump_state_counters(result, transition.new_state)

    # FSM, incident, notification event and auto-pause task commit together in
    # apply_fsm_transition. There is no crash window between those side effects.
    if task_id is not None:
        result.disable_tasks_created += 1

    if transition.transition_reason:
        result.transitions.append(f"{row.fb_ad_id}: {transition.transition_reason}")


def _bump_state_counters(result: CycleResult, new_state: str) -> None:
    """Инкрементирует счётчики стоячих инцидентов по итоговому FSM-состоянию строки.

    warning_sent — ад у порога (80% от стопа), нужен учащённый скан до развязки.
    stop_sent/claimed — стоп отправлен/кнопка нажата, но пауза ещё не подтверждена
    Meta (fsm_sync переведёт в disabled) — деньги капают, скан максимально частый.
    disabled/normal — инцидента нет, счётчики не трогаем.
    """
    if new_state == "warning_sent":
        result.ads_in_warning_state += 1
    elif new_state in ("stop_sent", "claimed"):
        result.ads_in_stop_state += 1


def _suppress_emit(transition, *, reason: str):
    """Возвращает копию FsmTransition с emit_alert=False (для snooze).

    MID-2 (money): снуз глушит ТОЛЬКО TG-уведомления (emit_alert), НЕ авто-стоп.
    create_disable_task сохраняется — заснуженный ад при STOP всё равно ставит
    pause-задачу (авто-стоп работает под снузом). Ранее здесь обнулялся
    create_disable_task → заснуженный убыточный ад крутился без стопа до истечения
    окна снуза (money-дыра): снуз задуман «не спамить алертами», а не «выключить
    авто-стоп».
    """
    from dataclasses import replace

    return replace(
        transition,
        emit_alert=False,
        transition_reason=f"{transition.transition_reason} [suppressed: {reason}]",
    )
