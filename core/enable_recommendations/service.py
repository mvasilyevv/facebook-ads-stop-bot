# -*- coding: utf-8 -*-
"""Сервис рекомендаций на включение выключенных объявлений."""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import AlertStage, EnableRecommendationLevel, EnableTaskStatus
from core.live_batch import (
    LIVE_BATCH_WINDOW,
    compute_live_batch_marker,
    is_within_live_batch,
    load_live_batch_bounds,
)
from core.models import (
    AdSnapshot,
    EnableRecommendationEvent,
    EnableTask,
    ObserverSettings,
    Offer,
    OfferRuleConfig,
)
from core.observer.service import build_metrics_json, build_rule_context
from core.observer.thresholds import extract_observer_threshold_values
from core.rules.evaluator import determine_enable_recommendation_level, evaluate_stop_rules
from core.rules.types import RuleEvaluation
from core.scanner.models import ScannedAdRow

RECOMMENDATION_DELIVERY_STATUSES = ("OFF", "NOT_DELIVERING")
OK_RECOMMENDATION_REASON_TITLE = "Строгая проверка пройдена"
OK_RECOMMENDATION_REASON_TEXT = (
    "Есть подтверждённые конверсии, и объявление проходит строгую проверку на включение."
)


@dataclass(slots=True, frozen=True)
class EnableRecommendationCandidate:
    """Кандидат на рекомендацию включения."""

    snapshot_id: _uuid.UUID | None
    offer_id: _uuid.UUID | None
    fb_ad_id: str
    ad_name: str
    delivery_status: str
    recommendation_level: EnableRecommendationLevel
    matched_rule_codes: list[str]
    reason_title: str | None
    reason_text: str | None
    metrics_json: dict[str, object]
    live_batch_started_at: datetime


@dataclass(slots=True, frozen=True)
class EnableTaskPromotionResult:
    """Результат перевода рекомендации в задачу на включение."""

    outcome: str
    fb_ad_id: str | None = None
    ad_name: str | None = None
    created_new: bool = False
    detail: str = ""
    task_id: str | None = None
    task_status: str | None = None


def _build_scanned_row_from_snapshot(snapshot: AdSnapshot) -> ScannedAdRow:
    """Преобразует snapshot в ScannedAdRow для evaluator."""
    return ScannedAdRow(
        fb_ad_id=snapshot.fb_ad_id,
        campaign_name=snapshot.campaign_name,
        adset_name=snapshot.adset_name,
        ad_name=snapshot.ad_name,
        delivery_status=snapshot.delivery_status,
        spend=Decimal(snapshot.spend),
        budget=getattr(snapshot, "budget", "") or "",
        reach=int(getattr(snapshot, "reach", 0) or 0),
        impressions=int(getattr(snapshot, "impressions", 0) or 0),
        clicks=int(snapshot.clicks or 0),
        cpc=Decimal(snapshot.cpc) if snapshot.cpc is not None else None,
        ctr=(Decimal(snapshot.ctr) if getattr(snapshot, "ctr", None) is not None else None),
        outbound_clicks=int(snapshot.outbound_clicks or 0),
        outbound_ctr=Decimal(snapshot.outbound_ctr) if snapshot.outbound_ctr is not None else None,
        landing_page_views=int(snapshot.landing_page_views or 0),
        cost_per_result=(
            Decimal(snapshot.cost_per_result)
            if getattr(snapshot, "cost_per_result", None) is not None
            else None
        ),
        cost_per_landing_page_view=(
            Decimal(snapshot.cost_per_landing_page_view)
            if snapshot.cost_per_landing_page_view is not None
            else None
        ),
        cpm=Decimal(snapshot.cpm) if snapshot.cpm is not None else None,
        frequency=Decimal(snapshot.frequency) if snapshot.frequency is not None else None,
        leads=int(snapshot.leads or 0),
        cost_per_lead=Decimal(snapshot.cost_per_lead)
        if snapshot.cost_per_lead is not None
        else None,
        registrations=int(snapshot.registrations or 0),
        cost_per_registration=(
            Decimal(snapshot.cost_per_registration)
            if snapshot.cost_per_registration is not None
            else None
        ),
        deposits=int(snapshot.deposits or 0),
        resolved_offer_code=snapshot.resolved_offer_code,
    )


def _build_recommendation_idempotency_key(
    *,
    fb_ad_id: str,
    live_batch_started_at: datetime,
) -> str:
    """Строит идемпотентный ключ recommendation event."""
    batch_key = live_batch_started_at.astimezone(UTC).isoformat()
    return f"enable_reco:{fb_ad_id}:{batch_key}"


def _build_enable_task_idempotency_key(recommendation_event_id: _uuid.UUID | str) -> str:
    """Строит идемпотентный ключ enable task для recommendation event."""
    return f"enable_reco_task:{recommendation_event_id}"


def _build_default_ok_recommendation_reason(
    *,
    row: ScannedAdRow,
) -> tuple[str, str]:
    """Возвращает дефолтную причину для OK-рекомендации."""
    if row.registrations >= 1 and row.deposits >= 1:
        recovery_parts = [f"подтверждённых депозитов: {row.deposits}"]
        if row.registrations >= 1:
            recovery_parts.append(f"регистраций: {row.registrations}")
        if row.cost_per_registration is not None:
            recovery_parts.append(f"CPR ${Decimal(row.cost_per_registration):.4f}")
        return (
            OK_RECOMMENDATION_REASON_TITLE,
            "Есть "
            + " · ".join(recovery_parts)
            + ". По текущим правилам блокирующих сигналов нет.",
        )

    if row.registrations >= 1 and row.cost_per_registration is not None:
        return (
            OK_RECOMMENDATION_REASON_TITLE,
            f"Есть завершённые регистрации: {row.registrations} · "
            f"CPR ${Decimal(row.cost_per_registration):.4f}. "
            "По текущим правилам блокирующих сигналов нет.",
        )

    if row.leads >= 1 and row.cost_per_lead is not None:
        return (
            OK_RECOMMENDATION_REASON_TITLE,
            f"Есть лиды: {row.leads} · "
            f"CPL ${Decimal(row.cost_per_lead):.4f}. "
            "По текущим правилам блокирующих сигналов нет.",
        )

    if row.clicks >= 1 and row.cpc is not None:
        return (
            OK_RECOMMENDATION_REASON_TITLE,
            f"Есть клики: {row.clicks} · "
            f"CPC ${Decimal(row.cpc):.4f}. "
            "По текущим правилам блокирующих сигналов нет.",
        )

    return OK_RECOMMENDATION_REASON_TITLE, OK_RECOMMENDATION_REASON_TEXT


def _is_zero_activity_row(row: ScannedAdRow) -> bool:
    """Отсекает OFF-строки с пустой активностью, чтобы не публиковать ложные рекомендации."""
    return (
        Decimal(row.spend) <= Decimal("0")
        and row.clicks <= 0
        and row.leads <= 0
        and row.registrations <= 0
        and row.deposits <= 0
    )


def _normalize_recommendation_reason(
    *,
    row: ScannedAdRow,
    recommendation_level: EnableRecommendationLevel,
    reason_title: str | None,
    reason_text: str | None,
) -> tuple[str | None, str | None]:
    """Возвращает человекочитаемую причину для recommendation event."""
    if recommendation_level == EnableRecommendationLevel.OK:
        default_title, default_text = _build_default_ok_recommendation_reason(row=row)
        return (
            reason_title or default_title,
            reason_text or default_text,
        )
    return reason_title, reason_text


async def _load_observer_rule_settings(session: AsyncSession) -> dict[str, Decimal]:
    """Загружает observer-настройки для evaluator."""
    row = await session.scalar(
        select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
    )
    return extract_observer_threshold_values(row)


async def _load_offer_rule_map(
    session: AsyncSession,
    *,
    offer_ids: list[_uuid.UUID],
) -> dict[_uuid.UUID, tuple[Offer, OfferRuleConfig | None]]:
    """Загружает offer + rule config для списка offer_id."""
    if not offer_ids:
        return {}

    result = await session.execute(
        select(Offer, OfferRuleConfig)
        .join(OfferRuleConfig, OfferRuleConfig.offer_id == Offer.id, isouter=True)
        .where(Offer.id.in_(offer_ids))
    )
    offer_map: dict[_uuid.UUID, tuple[Offer, OfferRuleConfig | None]] = {}
    for offer, rule_config in result.all():
        offer_map[offer.id] = (offer, rule_config)
    return offer_map


def _evaluate_enable_recommendation(
    *,
    row: ScannedAdRow,
    offer_cpa: Decimal,
    rule_config: object,
    observer_thresholds: dict[str, Decimal],
) -> tuple[EnableRecommendationLevel | None, RuleEvaluation]:
    """Вычисляет безопасный уровень рекомендации и исходную rule-оценку."""
    ctx = build_rule_context(
        cpa_amount=offer_cpa,
        warning_percent_of_stop=observer_thresholds["warning_percent_of_stop"],
        stop_percent_of_base=observer_thresholds["stop_percent_of_base"],
        rule_config=rule_config,
        observer_thresholds=observer_thresholds,
    )
    rule_evaluation = evaluate_stop_rules(row, ctx)
    recommendation_level = determine_enable_recommendation_level(
        row,
        ctx,
        stop_evaluation=rule_evaluation,
    )
    return recommendation_level, rule_evaluation


async def collect_enable_recommendation_candidates_for_snapshots(
    session: AsyncSession,
    *,
    snapshots: list[AdSnapshot],
    live_batch_started_at: datetime,
) -> list[EnableRecommendationCandidate]:
    """Переоценивает список snapshot и возвращает только актуальные рекомендации."""
    eligible_snapshots = [
        snapshot
        for snapshot in snapshots
        if snapshot.offer_id is not None
        and snapshot.delivery_status in RECOMMENDATION_DELIVERY_STATUSES
    ]
    if not eligible_snapshots:
        return []

    observer_thresholds = await _load_observer_rule_settings(session)
    offer_ids = [
        snapshot.offer_id for snapshot in eligible_snapshots if snapshot.offer_id is not None
    ]
    offer_map = await _load_offer_rule_map(session, offer_ids=offer_ids)

    candidates: list[EnableRecommendationCandidate] = []
    for snapshot in eligible_snapshots:
        offer_bundle = offer_map.get(snapshot.offer_id)
        if offer_bundle is None:
            continue
        offer, rule_config = offer_bundle
        if rule_config is None:
            continue

        row = _build_scanned_row_from_snapshot(snapshot)
        recommendation_level, evaluation = _evaluate_enable_recommendation(
            row=row,
            offer_cpa=Decimal(offer.cpa_amount),
            rule_config=rule_config,
            observer_thresholds=observer_thresholds,
        )
        if recommendation_level is None:
            continue
        if recommendation_level == EnableRecommendationLevel.OK and _is_zero_activity_row(row):
            continue
        if recommendation_level == EnableRecommendationLevel.WARNING:
            continue
        reason_title, reason_text = _normalize_recommendation_reason(
            row=row,
            recommendation_level=recommendation_level,
            reason_title=evaluation.reason_title,
            reason_text=evaluation.reason_text,
        )

        candidates.append(
            EnableRecommendationCandidate(
                snapshot_id=snapshot.id,
                offer_id=snapshot.offer_id,
                fb_ad_id=snapshot.fb_ad_id,
                ad_name=snapshot.ad_name,
                delivery_status=snapshot.delivery_status,
                recommendation_level=recommendation_level,
                matched_rule_codes=evaluation.matched_rule_codes,
                reason_title=reason_title,
                reason_text=reason_text,
                metrics_json=build_metrics_json(
                    row,
                    rule_summaries=[hit.summary for hit in evaluation.matched_hits],
                ),
                live_batch_started_at=live_batch_started_at,
            )
        )

    return candidates


async def collect_enable_recommendation_candidates(
    session: AsyncSession,
) -> tuple[datetime | None, list[EnableRecommendationCandidate]]:
    """Собирает кандидатов на recommendation event из текущего живого батча."""
    last_scan, batch_start = await load_live_batch_bounds(session)
    if last_scan is None or batch_start is None:
        return None, []

    live_batch_started_at = compute_live_batch_marker(last_scan, window=LIVE_BATCH_WINDOW)
    snapshots_result = await session.execute(
        select(AdSnapshot)
        .where(
            AdSnapshot.last_observed_at >= batch_start,
            AdSnapshot.delivery_status.in_(RECOMMENDATION_DELIVERY_STATUSES),
        )
        .order_by(AdSnapshot.last_observed_at.desc(), AdSnapshot.created_at.desc())
    )
    snapshots = snapshots_result.scalars().all()
    candidates = await collect_enable_recommendation_candidates_for_snapshots(
        session,
        snapshots=snapshots,
        live_batch_started_at=live_batch_started_at,
    )
    return live_batch_started_at, candidates


async def persist_enable_recommendation_candidates(
    session: AsyncSession,
    candidates: list[EnableRecommendationCandidate],
) -> list[EnableRecommendationEvent]:
    """Сохраняет новые recommendation events и обновляет payload у дублей."""
    created_events: list[EnableRecommendationEvent] = []
    for candidate in candidates:
        idempotency_key = _build_recommendation_idempotency_key(
            fb_ad_id=candidate.fb_ad_id,
            live_batch_started_at=candidate.live_batch_started_at,
        )
        existing = await session.scalar(
            select(EnableRecommendationEvent).where(
                EnableRecommendationEvent.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            existing.snapshot_id = candidate.snapshot_id
            existing.offer_id = candidate.offer_id
            existing.ad_name = candidate.ad_name
            existing.delivery_status = candidate.delivery_status
            existing.recommendation_level = candidate.recommendation_level
            existing.matched_rule_codes = list(candidate.matched_rule_codes)
            existing.reason_title = candidate.reason_title
            existing.reason_text = candidate.reason_text
            existing.metrics_json = dict(candidate.metrics_json)
            continue

        event = EnableRecommendationEvent(
            snapshot_id=candidate.snapshot_id,
            offer_id=candidate.offer_id,
            fb_ad_id=candidate.fb_ad_id,
            ad_name=candidate.ad_name,
            delivery_status=candidate.delivery_status,
            recommendation_level=candidate.recommendation_level,
            matched_rule_codes=candidate.matched_rule_codes,
            reason_title=candidate.reason_title,
            reason_text=candidate.reason_text,
            metrics_json=dict(candidate.metrics_json),
            live_batch_started_at=candidate.live_batch_started_at,
            idempotency_key=idempotency_key,
        )
        session.add(event)
        await session.flush()
        created_events.append(event)
    return created_events


async def attach_recommendation_telegram_delivery(
    session: AsyncSession,
    *,
    event_id: _uuid.UUID,
    chat_id: str,
    message_id: int,
) -> None:
    """Сохраняет ссылку на доставленное Telegram-сообщение для recommendation event."""
    event = await session.scalar(
        select(EnableRecommendationEvent).where(EnableRecommendationEvent.id == event_id)
    )
    if event is None:
        return
    event.telegram_chat_id = chat_id
    event.telegram_message_id = message_id


async def load_enable_recommendation_event(
    session: AsyncSession,
    event_id: str | _uuid.UUID,
) -> EnableRecommendationEvent | None:
    """Загружает recommendation event по id."""
    event_uuid = event_id if isinstance(event_id, _uuid.UUID) else _uuid.UUID(str(event_id))
    return await session.scalar(
        select(EnableRecommendationEvent).where(EnableRecommendationEvent.id == event_uuid)
    )


async def promote_recommendation_to_enable_task(
    session: AsyncSession,
    *,
    event_id: str | _uuid.UUID,
    requested_by_telegram_user_id: str | None = None,
    requested_by_username: str | None = None,
) -> EnableTaskPromotionResult:
    """Переоценивает recommendation event и создаёт или переиспользует EnableTask."""
    try:
        event = await load_enable_recommendation_event(session, event_id)
    except Exception:
        return EnableTaskPromotionResult(
            outcome="recommendation_not_found",
            detail="❌ Не удалось создать задачу на включение — рекомендация не найдена.",
        )

    if event is None:
        return EnableTaskPromotionResult(
            outcome="recommendation_not_found",
            detail="❌ Не удалось создать задачу на включение — рекомендация не найдена.",
        )

    cabinet_day_start = await session.scalar(
        select(ObserverSettings.cabinet_day_started_at).where(
            ObserverSettings.singleton_key == "default"
        )
    )
    if cabinet_day_start is not None and event.live_batch_started_at < cabinet_day_start:
        return EnableTaskPromotionResult(
            outcome="stale_cabinet_day",
            fb_ad_id=event.fb_ad_id,
            ad_name=event.ad_name,
            detail="⚠️ Рекомендация устарела: она была создана в прошлых сутках кабинета.",
        )

    snapshot = None
    if event.snapshot_id is not None:
        snapshot = await session.scalar(
            select(AdSnapshot).where(AdSnapshot.id == event.snapshot_id)
        )
    if snapshot is None:
        snapshot = await session.scalar(
            select(AdSnapshot).where(AdSnapshot.fb_ad_id == event.fb_ad_id)
        )
    if snapshot is None:
        return EnableTaskPromotionResult(
            outcome="snapshot_not_found",
            fb_ad_id=event.fb_ad_id,
            ad_name=event.ad_name,
            detail="❌ Не удалось создать задачу на включение — объявление не найдено.",
        )

    last_scan, batch_start = await load_live_batch_bounds(session)
    if (
        last_scan is None
        or batch_start is None
        or not is_within_live_batch(snapshot.last_observed_at, batch_start)
    ):
        return EnableTaskPromotionResult(
            outcome="stale_batch",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=snapshot.ad_name,
            detail="⚠️ Рекомендация устарела: объявление уже не входит в актуальный срез.",
        )

    if snapshot.delivery_status not in RECOMMENDATION_DELIVERY_STATUSES:
        return EnableTaskPromotionResult(
            outcome="not_disabled",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=snapshot.ad_name,
            detail="⚠️ Рекомендация устарела: объявление уже не выключено.",
        )

    if snapshot.offer_id is None:
        return EnableTaskPromotionResult(
            outcome="offer_not_found",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=snapshot.ad_name,
            detail="⚠️ Рекомендация устарела: для объявления больше не найден оффер.",
        )

    offer_bundle = (await _load_offer_rule_map(session, offer_ids=[snapshot.offer_id])).get(
        snapshot.offer_id
    )
    if offer_bundle is None or offer_bundle[1] is None:
        return EnableTaskPromotionResult(
            outcome="rules_not_found",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=snapshot.ad_name,
            detail="⚠️ Рекомендация устарела: для оффера нет актуальных правил.",
        )

    observer_thresholds = await _load_observer_rule_settings(session)
    offer, rule_config = offer_bundle
    recommendation_level, evaluation = _evaluate_enable_recommendation(
        row=_build_scanned_row_from_snapshot(snapshot),
        offer_cpa=Decimal(offer.cpa_amount),
        rule_config=rule_config,
        observer_thresholds=observer_thresholds,
    )
    if evaluation.stage == AlertStage.STOP:
        return EnableTaskPromotionResult(
            outcome="blocked_stop",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=snapshot.ad_name,
            detail="⚠️ Рекомендация устарела: объявление сейчас уже в стоп-зоне.",
        )
    if recommendation_level == EnableRecommendationLevel.WARNING:
        return EnableTaskPromotionResult(
            outcome="blocked_warning",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=snapshot.ad_name,
            detail="⚠️ Рекомендация устарела: у объявления сейчас активен warning.",
        )
    if recommendation_level is None:
        return EnableTaskPromotionResult(
            outcome="blocked_recommendation",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=snapshot.ad_name,
            detail="⚠️ Рекомендация устарела: объявление больше не проходит строгую проверку на включение.",
        )

    idempotency_key = _build_enable_task_idempotency_key(event.id)
    existing_task = await session.scalar(
        select(EnableTask).where(EnableTask.idempotency_key == idempotency_key)
    )
    if existing_task is not None:
        if existing_task.status in (
            EnableTaskStatus.FAILED,
            EnableTaskStatus.CANCELLED,
        ):
            existing_task.status = EnableTaskStatus.PENDING
            existing_task.attempt_count = 0
            existing_task.next_retry_at = None
            existing_task.last_error = None
            existing_task.completed_at = None
            if requested_by_telegram_user_id is not None:
                existing_task.requested_by_telegram_user_id = requested_by_telegram_user_id
            if requested_by_username is not None:
                existing_task.requested_by_username = requested_by_username
            return EnableTaskPromotionResult(
                outcome="requeued",
                fb_ad_id=snapshot.fb_ad_id,
                ad_name=snapshot.ad_name,
                created_new=False,
                detail="✅ Существующая задача на включение возвращена в очередь.",
                task_id=str(existing_task.id),
                task_status=existing_task.status.value,
            )

        return EnableTaskPromotionResult(
            outcome="existing",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=snapshot.ad_name,
            created_new=False,
            detail="ℹ️ Задача на включение уже была создана ранее.",
            task_id=str(existing_task.id),
            task_status=existing_task.status.value,
        )

    task = EnableTask(
        snapshot_id=snapshot.id,
        recommendation_event_id=event.id,
        fb_ad_id=snapshot.fb_ad_id,
        ad_name=snapshot.ad_name,
        idempotency_key=idempotency_key,
        requested_by_telegram_user_id=requested_by_telegram_user_id,
        requested_by_username=requested_by_username,
    )
    session.add(task)
    await session.flush()
    return EnableTaskPromotionResult(
        outcome="created",
        fb_ad_id=snapshot.fb_ad_id,
        ad_name=snapshot.ad_name,
        created_new=True,
        detail="✅ Создана задача на включение.",
        task_id=str(task.id),
        task_status=task.status.value,
    )
