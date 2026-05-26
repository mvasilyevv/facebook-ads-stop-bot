# -*- coding: utf-8 -*-
"""Сервис рекомендаций на включение выключенных объявлений."""

from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.domain import AlertStage, DisableTaskStatus, EnableRecommendationLevel, EnableTaskStatus
from core.live_batch import (
    LIVE_BATCH_WINDOW,
    compute_live_batch_marker,
    is_within_live_batch,
    load_live_batch_bounds,
)
from core.models import (
    AdSnapshot,
    AlertEvent,
    DisableTask,
    EnableRecommendationEvent,
    EnableTask,
    FbAd,
    FbAdset,
    Offer,
    OfferRuleConfig,
)
from core.observer.service import build_metrics_json, build_rule_context
from core.rules.evaluator import determine_enable_recommendation_level, evaluate_stop_rules
from core.rules.types import RuleEvaluation
from core.scanner.models import ScannedAdRow
from core.settings_queries import get_observer_settings

logger = logging.getLogger(__name__)

RECOMMENDATION_DELIVERY_STATUSES = ("OFF",)
OK_RECOMMENDATION_REASON_TITLE = "Строгая проверка пройдена"
OK_RECOMMENDATION_REASON_TEXT = (
    "Есть подтверждённые конверсии, и объявление проходит строгую проверку на включение."
)
AUTO_DISABLE_REQUEST_USERNAME = "bot_auto_stop"
AUTO_ENABLE_REQUEST_USERNAME = "auto"


@dataclass(slots=True, frozen=True)
class EnableRecommendationCandidate:
    """Кандидат на рекомендацию включения."""

    ad_id: _uuid.UUID | None
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


def _snapshot_ad_name(snapshot: AdSnapshot) -> str:
    """Имя объявления через цепочку fb_ad."""
    return snapshot.fb_ad.ad_name if snapshot.fb_ad else ""


def _snapshot_campaign_name(snapshot: AdSnapshot) -> str:
    """Имя кампании через цепочку fb_ad → adset → campaign."""
    fb_ad = snapshot.fb_ad
    if fb_ad and fb_ad.adset and fb_ad.adset.campaign:
        return fb_ad.adset.campaign.campaign_name
    return ""


def _snapshot_adset_name(snapshot: AdSnapshot) -> str:
    """Имя адсета через цепочку fb_ad → adset."""
    fb_ad = snapshot.fb_ad
    if fb_ad and fb_ad.adset:
        return fb_ad.adset.adset_name
    return ""


def _snapshot_offer_id(snapshot: AdSnapshot) -> _uuid.UUID | None:
    """offer_id через цепочку fb_ad → adset → campaign."""
    fb_ad = snapshot.fb_ad
    if fb_ad and fb_ad.adset and fb_ad.adset.campaign:
        return fb_ad.adset.campaign.offer_id
    return None


def _snapshot_offer_code(snapshot: AdSnapshot) -> str | None:
    """offer_code через цепочку fb_ad → adset → campaign."""
    fb_ad = snapshot.fb_ad
    if fb_ad and fb_ad.adset and fb_ad.adset.campaign:
        return fb_ad.adset.campaign.offer_code
    return None


def _snapshot_selectinload() -> selectinload:
    """Стандартная цепочка eager-load для snapshot → fb_ad → adset → campaign."""
    return selectinload(AdSnapshot.fb_ad).selectinload(FbAd.adset).selectinload(FbAdset.campaign)


def _recommendation_event_selectinload() -> selectinload:
    """Стандартная цепочка eager-load для recommendation event → fb_ad → adset → campaign."""
    return (
        selectinload(EnableRecommendationEvent.fb_ad)
        .selectinload(FbAd.adset)
        .selectinload(FbAdset.campaign)
    )


def _build_scanned_row_from_snapshot(snapshot: AdSnapshot) -> ScannedAdRow:
    """Преобразует snapshot в ScannedAdRow для evaluator."""
    return ScannedAdRow(
        fb_ad_id=snapshot.fb_ad_id,
        campaign_name=_snapshot_campaign_name(snapshot),
        adset_name=_snapshot_adset_name(snapshot),
        ad_name=_snapshot_ad_name(snapshot),
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
        resolved_offer_code=_snapshot_offer_code(snapshot),
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


async def _has_manual_disable_auto_block(
    session: AsyncSession,
    *,
    ad_id: _uuid.UUID,
    cabinet_day_started_at: datetime | None,
) -> bool:
    """Проверяет, что объявление вручную отключали в текущих сутках кабинета."""
    conditions = [
        DisableTask.ad_id == ad_id,
        DisableTask.status == DisableTaskStatus.SUCCEEDED,
        or_(
            DisableTask.requested_by_username.is_(None),
            DisableTask.requested_by_username != AUTO_DISABLE_REQUEST_USERNAME,
        ),
    ]
    if cabinet_day_started_at is not None:
        conditions.append(DisableTask.created_at >= cabinet_day_started_at)

    result = await session.execute(select(DisableTask.id).where(and_(*conditions)).limit(1))
    return result.scalar_one_or_none() is not None


async def _has_auto_enable_cooldown_block(
    session: AsyncSession,
    *,
    ad_id: _uuid.UUID,
    cabinet_day_started_at: datetime | None,
) -> tuple[bool, str | None]:
    """Cooldown auto-enable: блокирует, если в текущем кабинетном дне был disable или STOP.

    Защищает от loop'а auto-stop → auto-enable → auto-stop. Возвращает (blocked, reason),
    где reason — короткая строка для лога/детали ответа. Ручное создание EnableTask
    через Telegram-кнопку не вызывает эту функцию — проверка нужна только для авто-пути.

    Порядок проверок (важно для совместимости с существующими unit-тестами):
    сначала DisableTask, потом AlertEvent.
    """
    # 1) Успешный DisableTask в текущем кабинетном дне — независимо от инициатора.
    # Раньше тут стояло требование requested_by_username != bot_auto_stop, и
    # auto-disable не блокировал auto-enable → возникал loop. Теперь блокируем оба.
    disable_conditions = [
        DisableTask.ad_id == ad_id,
        DisableTask.status == DisableTaskStatus.SUCCEEDED,
    ]
    if cabinet_day_started_at is not None:
        disable_conditions.append(DisableTask.created_at >= cabinet_day_started_at)
    disable_result = await session.execute(
        select(DisableTask.id, DisableTask.requested_by_username)
        .where(and_(*disable_conditions))
        .limit(1)
    )
    disable_row = disable_result.first()
    if disable_row is not None:
        username = disable_row[1]
        if username == AUTO_DISABLE_REQUEST_USERNAME:
            return True, "auto_disable"
        return True, "manual_disable"

    # 2) AlertEvent со stage=STOP — на случай если STOP сработал, а DisableTask ещё не успел.
    stop_conditions = [
        AlertEvent.ad_id == ad_id,
        AlertEvent.stage == AlertStage.STOP,
    ]
    if cabinet_day_started_at is not None:
        stop_conditions.append(AlertEvent.created_at >= cabinet_day_started_at)
    stop_result = await session.execute(
        select(AlertEvent.id).where(and_(*stop_conditions)).limit(1)
    )
    if stop_result.scalar_one_or_none() is not None:
        return True, "stop_alert"

    return False, None


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
    adaptive_cpa: Decimal | None = None,
    use_adaptive_cpa: bool = False,
) -> tuple[EnableRecommendationLevel | None, RuleEvaluation]:
    """Вычисляет безопасный уровень рекомендации и исходную rule-оценку."""
    ctx = build_rule_context(
        cpa_amount=offer_cpa,
        rule_config=rule_config,
        frequency_current=Decimal(str(row.frequency)) if row.frequency is not None else None,
        impressions=int(row.impressions) if row.impressions is not None else None,
        reach=int(row.reach) if getattr(row, "reach", None) is not None else None,
        adaptive_cpa=adaptive_cpa,
        use_adaptive_cpa=use_adaptive_cpa,
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
        if _snapshot_offer_id(snapshot) is not None
        and snapshot.delivery_status in RECOMMENDATION_DELIVERY_STATUSES
    ]
    if not eligible_snapshots:
        return []

    offer_ids = [
        oid for snapshot in eligible_snapshots if (oid := _snapshot_offer_id(snapshot)) is not None
    ]
    offer_map = await _load_offer_rule_map(session, offer_ids=offer_ids)

    candidates: list[EnableRecommendationCandidate] = []
    for snapshot in eligible_snapshots:
        s_offer_id = _snapshot_offer_id(snapshot)
        offer_bundle = offer_map.get(s_offer_id) if s_offer_id else None
        if offer_bundle is None:
            continue
        offer, rule_config = offer_bundle
        if rule_config is None:
            continue

        row = _build_scanned_row_from_snapshot(snapshot)
        use_adaptive = bool(getattr(rule_config, "use_adaptive_cpa", False))
        # adaptive_cpa для OFF-снэпшотов недоступен: rolling median считается
        # observer'ом по горячему батчу и в DB не сохраняется. Оставляем None —
        # build_rule_context при use_adaptive_cpa=True и adaptive_cpa=None
        # fallback'ит на статичный cpa_amount.
        recommendation_level, evaluation = _evaluate_enable_recommendation(
            row=row,
            offer_cpa=Decimal(offer.cpa_amount),
            rule_config=rule_config,
            adaptive_cpa=None,
            use_adaptive_cpa=use_adaptive,
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
                ad_id=snapshot.ad_id,
                snapshot_id=snapshot.id,
                offer_id=s_offer_id,
                fb_ad_id=snapshot.fb_ad_id,
                ad_name=_snapshot_ad_name(snapshot),
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
        .options(_snapshot_selectinload())
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
            # Если изменился level или заголовок причины — текст Telegram-сообщения
            # устарел. Сбрасываем chat_id/message_id, чтобы доставка переотправила
            # (load_pending_enable_recommendation_events фильтрует по IS NULL).
            prev_level = getattr(existing, "recommendation_level", None)
            prev_reason_title = getattr(existing, "reason_title", None)
            level_changed = prev_level != candidate.recommendation_level
            reason_changed = prev_reason_title != candidate.reason_title
            existing.snapshot_id = candidate.snapshot_id
            existing.offer_id = candidate.offer_id
            existing.delivery_status = candidate.delivery_status
            existing.recommendation_level = candidate.recommendation_level
            existing.matched_rule_codes = list(candidate.matched_rule_codes)
            existing.reason_title = candidate.reason_title
            existing.reason_text = candidate.reason_text
            existing.metrics_json = dict(candidate.metrics_json)
            if level_changed or reason_changed:
                existing.telegram_chat_id = None
                existing.telegram_message_id = None
            continue

        event = EnableRecommendationEvent(
            ad_id=candidate.ad_id,
            snapshot_id=candidate.snapshot_id,
            offer_id=candidate.offer_id,
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


async def load_pending_enable_recommendation_events(
    session: AsyncSession,
    *,
    live_batch_started_at: datetime,
) -> list[EnableRecommendationEvent]:
    """Загружает рекомендации текущего live-batch без сохранённой Telegram-доставки."""
    result = await session.execute(
        select(EnableRecommendationEvent)
        .options(_recommendation_event_selectinload())
        .where(
            EnableRecommendationEvent.live_batch_started_at == live_batch_started_at,
            or_(
                EnableRecommendationEvent.telegram_chat_id.is_(None),
                EnableRecommendationEvent.telegram_message_id.is_(None),
            ),
        )
        .order_by(EnableRecommendationEvent.created_at.asc())
    )
    return result.scalars().unique().all()


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

    # Загружаем fb_ad для event, чтобы получить fb_ad_id и ad_name
    event_fb_ad = await session.scalar(
        select(FbAd)
        .options(selectinload(FbAd.adset).selectinload(FbAdset.campaign))
        .where(FbAd.id == event.ad_id)
    )
    event_fb_ad_id = event_fb_ad.fb_ad_id if event_fb_ad else ""
    event_ad_name = event_fb_ad.ad_name if event_fb_ad else ""

    _obs_settings = await get_observer_settings(session)
    cabinet_day_start = _obs_settings.cabinet_day_started_at if _obs_settings else None
    if cabinet_day_start is not None and event.live_batch_started_at < cabinet_day_start:
        return EnableTaskPromotionResult(
            outcome="stale_cabinet_day",
            fb_ad_id=event_fb_ad_id,
            ad_name=event_ad_name,
            detail="⚠️ Рекомендация устарела: она была создана в прошлых сутках кабинета.",
        )

    snapshot = None
    if event.snapshot_id is not None:
        snapshot = await session.scalar(
            select(AdSnapshot)
            .options(_snapshot_selectinload())
            .where(AdSnapshot.id == event.snapshot_id)
        )
    if snapshot is None:
        snapshot = await session.scalar(
            select(AdSnapshot)
            .options(_snapshot_selectinload())
            .where(AdSnapshot.fb_ad_id == event_fb_ad_id)
        )
    if snapshot is None:
        return EnableTaskPromotionResult(
            outcome="snapshot_not_found",
            fb_ad_id=event_fb_ad_id,
            ad_name=event_ad_name,
            detail="❌ Не удалось создать задачу на включение — объявление не найдено.",
        )

    if requested_by_username == AUTO_ENABLE_REQUEST_USERNAME:
        cooldown_blocked, cooldown_reason = await _has_auto_enable_cooldown_block(
            session,
            ad_id=snapshot.ad_id,
            cabinet_day_started_at=cabinet_day_start,
        )
        if cooldown_blocked:
            if cooldown_reason == "manual_disable":
                outcome = "blocked_manual_disable"
                detail = (
                    "⚠️ Авто-включение заблокировано: объявление было отключено вручную "
                    "в текущих сутках кабинета."
                )
            elif cooldown_reason == "auto_disable":
                outcome = "blocked_auto_disable_cooldown"
                detail = (
                    "⚠️ Авто-включение заблокировано: объявление уже было авто-отключено "
                    "в текущих сутках кабинета."
                )
            else:
                outcome = "blocked_stop_cooldown"
                detail = (
                    "⚠️ Авто-включение заблокировано: в текущих сутках кабинета уже был STOP-алёрт."
                )
            return EnableTaskPromotionResult(
                outcome=outcome,
                fb_ad_id=snapshot.fb_ad_id,
                ad_name=_snapshot_ad_name(snapshot),
                detail=detail,
            )

    # Кэшируем нормализованные поля для snapshot
    s_ad_name = _snapshot_ad_name(snapshot)
    s_offer_id = _snapshot_offer_id(snapshot)

    last_scan, batch_start = await load_live_batch_bounds(session)
    if (
        last_scan is None
        or batch_start is None
        or not is_within_live_batch(snapshot.last_observed_at, batch_start)
    ):
        return EnableTaskPromotionResult(
            outcome="stale_batch",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=s_ad_name,
            detail="⚠️ Рекомендация устарела: объявление уже не входит в актуальный срез.",
        )

    if snapshot.delivery_status not in RECOMMENDATION_DELIVERY_STATUSES:
        return EnableTaskPromotionResult(
            outcome="not_disabled",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=s_ad_name,
            detail="⚠️ Рекомендация устарела: объявление уже не выключено.",
        )

    if s_offer_id is None:
        return EnableTaskPromotionResult(
            outcome="offer_not_found",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=s_ad_name,
            detail="⚠️ Рекомендация устарела: для объявления больше не найден оффер.",
        )

    offer_bundle = (await _load_offer_rule_map(session, offer_ids=[s_offer_id])).get(s_offer_id)
    if offer_bundle is None or offer_bundle[1] is None:
        return EnableTaskPromotionResult(
            outcome="rules_not_found",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=s_ad_name,
            detail="⚠️ Рекомендация устарела: для оффера нет актуальных правил.",
        )

    offer, rule_config = offer_bundle
    use_adaptive = bool(getattr(rule_config, "use_adaptive_cpa", False))
    recommendation_level, evaluation = _evaluate_enable_recommendation(
        row=_build_scanned_row_from_snapshot(snapshot),
        offer_cpa=Decimal(offer.cpa_amount),
        rule_config=rule_config,
        adaptive_cpa=None,
        use_adaptive_cpa=use_adaptive,
    )
    if evaluation.stage == AlertStage.STOP:
        return EnableTaskPromotionResult(
            outcome="blocked_stop",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=s_ad_name,
            detail="⚠️ Рекомендация устарела: объявление сейчас уже в стоп-зоне.",
        )
    if recommendation_level == EnableRecommendationLevel.WARNING:
        return EnableTaskPromotionResult(
            outcome="blocked_warning",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=s_ad_name,
            detail="⚠️ Рекомендация устарела: у объявления сейчас активен warning.",
        )
    if recommendation_level is None:
        return EnableTaskPromotionResult(
            outcome="blocked_recommendation",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=s_ad_name,
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
                ad_name=s_ad_name,
                created_new=False,
                detail="✅ Существующая задача на включение возвращена в очередь.",
                task_id=str(existing_task.id),
                task_status=existing_task.status.value,
            )

        return EnableTaskPromotionResult(
            outcome="existing",
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=s_ad_name,
            created_new=False,
            detail="ℹ️ Задача на включение уже была создана ранее.",
            task_id=str(existing_task.id),
            task_status=existing_task.status.value,
        )

    task = EnableTask(
        ad_id=snapshot.ad_id,
        snapshot_id=snapshot.id,
        recommendation_event_id=event.id,
        idempotency_key=idempotency_key,
        requested_by_telegram_user_id=requested_by_telegram_user_id,
        requested_by_username=requested_by_username,
    )
    session.add(task)
    await session.flush()
    return EnableTaskPromotionResult(
        outcome="created",
        fb_ad_id=snapshot.fb_ad_id,
        ad_name=s_ad_name,
        created_new=True,
        detail="✅ Создана задача на включение.",
        task_id=str(task.id),
        task_status=task.status.value,
    )


async def cleanup_orphaned_recommendation_events(session: AsyncSession) -> int:
    """Удаляет EnableRecommendationEvent старше 7 дней без связанной EnableTask.

    Returns:
        Количество удалённых записей.
    """
    cutoff = datetime.now(UTC) - timedelta(days=7)
    result = await session.execute(
        delete(EnableRecommendationEvent).where(
            EnableRecommendationEvent.created_at < cutoff,
            ~EnableRecommendationEvent.id.in_(
                select(EnableTask.recommendation_event_id).where(
                    EnableTask.recommendation_event_id.is_not(None)
                )
            ),
        )
    )
    deleted_count = result.rowcount
    if deleted_count > 0:
        logger.debug("Удалено %s orphaned EnableRecommendationEvent старше 7 дней", deleted_count)
    return deleted_count
