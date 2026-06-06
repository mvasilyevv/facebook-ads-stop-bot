# -*- coding: utf-8 -*-
"""Главный pipeline observer: один scan-цикл (set of ScannedAdRow) → персист и FSM.

Минимальный путь, без эскалаторов хрупкости и adaptive CPA — baseline = cpa_threshold
из offer_rules. Расширения добавим отдельно.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.adset_pro.queries import load_external_deposits_batch
from core.observer.queries import (
    OfferRules,
    campaign_matches_owner,
    load_active_offers,
    load_alert_state_by_fb_ad_id,
    match_offer_for_ad,
)
from core.observer.state_machine import FsmInput, decide
from core.observer.writers import (
    apply_fsm_transition,
    insert_metrics,
    maybe_create_disable_task,
    upsert_catalog_hierarchy,
)
from core.rules.evaluator import evaluate_stop_rules
from core.rules.types import RuleContext, RuleEvaluation
from core.scanner.models import ScannedAdRow

logger = logging.getLogger(__name__)


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
    disable_tasks_created: int = 0
    transitions: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


def build_rule_context(
    offer: OfferRules,
    *,
    external_deposits: int = 0,
    frequency_current: Decimal | None = None,
    impressions: int | None = None,
    reach: int | None = None,
) -> RuleContext:
    """OfferRules → RuleContext с минимальным набором параметров.

    cpa_amount = cpa_threshold из offer_rules; если не задан — Decimal('100') как
    нейтральный default чтобы правила не падали по делению на ноль. Без adaptive.
    external_deposits — из AdSet.pro трекера, защищают от STOP при наличии депозита,
    которого Meta Ads Manager ещё не видит.

    frequency-anomaly (правило 7, #37) — opt-in per-offer через offer.frequency_threshold:
    NULL/0 → правило выключено для этого оффера; задан → stop-порог = frequency_threshold,
    warning — свёртка 80% (как у CPC/CPL/CPR). Фаза 1: только абсолютный порог, без
    истории frequency_1h_ago (рост за час не считаем).

    impressions/reach (M1): кладём в RuleContext ВСЕГДА. Они нужны frequency-правилу И
    guardrail'у (cpc/cpl/cpr при 0 событий) как sanity-минимум показов — не стопать на
    статистически нерепрезентативных (<min_impressions) показах. Раньше передавались
    только при freq_enabled, из-за чего включение frequency на оффере незаметно меняло
    поведение guardrail (непоследовательность). Практически безопасно: при малых показах
    спенд обычно ниже guardrail-порога, так что стоп-решения не меняются.
    """
    cpa = offer.cpa_threshold or Decimal("100")
    # Чувствительность per-offer: при каком % правила срабатывает стоп/ворнинг.
    # Дефолт 80/80 (как было захардкожено) при отсутствии offer_rules-строки.
    warning_pct = offer.warning_percent_of_stop or Decimal("80")
    stop_pct = offer.stop_percent_of_rule or Decimal("80")

    freq_threshold = offer.frequency_threshold
    freq_enabled = freq_threshold is not None and freq_threshold > 0
    if freq_enabled:
        freq_stop = Decimal(freq_threshold)
        freq_warning = (freq_stop * warning_pct / Decimal("100")).quantize(Decimal("0.01"))
    else:
        # Дефолты RuleContext (не используются при enabled=False, заданы для валидности).
        freq_stop = Decimal("3.5")
        freq_warning = Decimal("2.5")

    return RuleContext(
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
    )


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
) -> CycleResult:
    """Один scan-цикл. Идемпотентен по (ad_id, cycle_ts) и (idempotency_key).

    Args:
        rows: список ScannedAdRow которые пришли от scanner gRPC.
        scan_id: монотонный счётчик (для аналитики и связи с alert_events).
        cycle_ts: общий timestamp цикла — используется в ad_metrics + alert_events.
                   Дефолт — NOW.
        owner_tag: owner-scoping. Если задан — строки кампаний без этого тега
                   полностью игнорируются (не пишем метрики, не оцениваем правила,
                   не дизейблим). NULL — фильтр выключен. Защита от работы с чужими
                   кампаниями в общем рекламном кабинете.

    Returns:
        CycleResult с метриками цикла.
    """
    if cycle_ts is None:
        cycle_ts = datetime.now(timezone.utc)

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
    external_deposits = await load_external_deposits_batch(engine, fb_ad_ids=fb_ids)

    # 4. Прогоняем каждую строку
    for row in rows:
        try:
            await _process_one_row(
                engine,
                row=row,
                offers=offers,
                states=states,
                external_deposits=external_deposits,
                scan_id=scan_id,
                cycle_ts=cycle_ts,
                result=result,
                owner_tag=owner_tag,
            )
        except Exception:
            logger.exception(
                "observer: ошибка обработки fb_ad_id=%s, продолжаю остальные",
                row.fb_ad_id,
            )

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
) -> None:
    """Обработка одной строки. Вынесено отдельно ради читаемости + try/except в caller'е."""

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
            fb_adset_id=None,
            adset_name=row.adset_name,
            fb_campaign_id=row.campaign_id or None,
            campaign_name=row.campaign_name,
            offer_id=None,
            delivery_status=row.delivery_status,
        )
        if await insert_metrics(
            engine,
            ad_id=ad_id,
            cycle_ts=cycle_ts,
            scan_id=scan_id,
            metrics=_row_to_metrics_dict(row),
        ):
            return
        return

    result.rows_with_offer += 1

    # --- Catalog upsert ---
    ad_id = await upsert_catalog_hierarchy(
        engine,
        fb_ad_id=row.fb_ad_id,
        ad_name=row.ad_name,
        fb_adset_id=None,
        adset_name=row.adset_name,
        fb_campaign_id=row.campaign_id or None,
        campaign_name=row.campaign_name,
        offer_id=matched_offer.offer_id,
        delivery_status=row.delivery_status,
    )

    # --- Метрики (партиционированная таблица) ---
    metrics = _row_to_metrics_dict(row)
    await insert_metrics(engine, ad_id=ad_id, cycle_ts=cycle_ts, scan_id=scan_id, metrics=metrics)

    # --- Оценка правил (одна функция возвращает оба уровня severity) ---
    ad_external_deposits = external_deposits.get(row.fb_ad_id, 0) if row.fb_ad_id else 0
    ctx = build_rule_context(
        matched_offer,
        external_deposits=ad_external_deposits,
        frequency_current=row.frequency,
        impressions=row.impressions,
        reach=row.reach,
    )
    evaluation = evaluate_stop_rules(row, ctx)
    stop_codes = tuple(evaluation.stop_rule_codes)
    warning_codes = tuple(evaluation.warning_rule_codes)

    # --- FSM ---
    current = states.get(row.fb_ad_id)
    fsm_input = FsmInput(
        current_state=current.alert_state if current else "normal",
        current_stage=current.current_stage if current else None,
        current_open_token=current.open_state_token if current else None,
        warning_rule_codes=warning_codes,
        stop_rule_codes=stop_codes,
    )
    transition = decide(fsm_input)

    # --- Snooze: пропускаем emit если ад заснужен ---
    if current and current.snoozed_until and current.snoozed_until > cycle_ts:
        # FSM-состояние всё равно обновим (state может deescalate), но не emit
        transition = _suppress_emit(transition, reason="snoozed")

    # --- Persist FSM + event ---
    # Детали сработавших правил (value/threshold) → в alert_events.metrics_json,
    # чтобы renderer показал «CPL $9.56 (стоп $3.00)» без реконструкции свёрнутых
    # порогов. В ad_metrics не попадает — insert_metrics уже выполнен выше.
    hits_payload = _hits_payload(evaluation)
    metrics_for_event = {**metrics, "_hits": hits_payload} if hits_payload else metrics
    await apply_fsm_transition(
        engine,
        ad_id=ad_id,
        transition=transition,
        metrics_snapshot=metrics_for_event,
        scan_id=scan_id,
    )

    if transition.emit_alert:
        if transition.alert_stage == "warning":
            result.alerts_warning += 1
        elif transition.alert_stage == "stop":
            result.alerts_stop += 1

    # --- Outbox: disable task если auto-stop ---
    task_id = await maybe_create_disable_task(
        engine,
        transition=transition,
        fb_ad_id=row.fb_ad_id,
        open_token=transition.new_open_token,
    )
    if task_id is not None:
        result.disable_tasks_created += 1

    if transition.transition_reason:
        result.transitions.append(f"{row.fb_ad_id}: {transition.transition_reason}")


def _suppress_emit(transition, *, reason: str):
    """Возвращает копию FsmTransition с emit_alert=False (для snooze)."""
    from dataclasses import replace

    return replace(
        transition,
        emit_alert=False,
        create_disable_task=False,
        transition_reason=f"{transition.transition_reason} [suppressed: {reason}]",
    )
