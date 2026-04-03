# -*- coding: utf-8 -*-
"""FastAPI роутер для dashboard, метрик и аналитики."""

import logging
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import (
    ActiveIncidentSchema,
    AdDiagnosticsSchema,
    AdSnapshotSchema,
    AlertEventSchema,
    ChartDataSchema,
    CreateDisableTaskRequest,
    CurrentEnableRecommendationRow,
    DashboardBatchSchema,
    DashboardPerformanceCampaignSchema,
    DashboardPerformanceFunnelStepSchema,
    DashboardPerformanceSchema,
    DashboardPerformanceSummarySchema,
    DashboardPerformanceTimelinePointSchema,
    DashboardStatsSchema,
    DisableTaskSchema,
    EnableRecommendationEventSchema,
    EnableTaskSchema,
    SpendHistoryPoint,
    _normalize_enable_recommendation_reason,
    _offer_code_lookup_key,
)
from core.config import get_settings
from core.diagnostics import build_ad_quality_diagnostics, compute_cpm_baselines_by_offer
from core.disable_tasks import (
    DISABLE_TASK_STALE_TIMEOUT,
    SILENT_DISABLE_INCIDENT_RETRY_LIMIT,
    is_delivery_disabled,
)
from core.domain import (
    AlertStage,
    AlertState,
    DisableTaskStatus,
    EnableRecommendationLevel,
    EnableTaskStatus,
)
from core.enable_recommendations.service import (
    RECOMMENDATION_DELIVERY_STATUSES,
    EnableRecommendationCandidate,
    collect_enable_recommendation_candidates_for_snapshots,
    promote_recommendation_to_enable_task,
)
from core.live_batch import compute_live_batch_marker, is_within_live_batch, load_live_batch_bounds
from core.models import (
    AdSnapshot,
    AlertEvent,
    CabinetDayArchive,
    DisableTask,
    EnableRecommendationEvent,
    EnableTask,
    ObserverSettings,
    Offer,
    OfferRuleConfig,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["dashboard"])


def _performance_cutoff(period: str, now: datetime) -> datetime:
    """Возвращает нижнюю границу периода для performance-дашборда."""
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "yesterday":
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return today_start - timedelta(days=1)
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    raise ValueError(f"Неизвестный период: {period}")


def _current_scan_cutoff(last_scan: datetime | None) -> datetime:
    """Возвращает начало актуальной скан-сессии."""
    if last_scan is None:
        return datetime.now(UTC)
    return last_scan - timedelta(minutes=30)


def _serialize_optional_datetime(value: datetime | None) -> str | None:
    """Сериализует дату в ISO-формат."""
    return value.isoformat() if value else None


def _serialize_optional_decimal(value: object | None, precision: int) -> str | None:
    """Сериализует Decimal-подобное значение в строку нужной точности."""
    if value is None:
        return None
    return f"{Decimal(str(value)):.{precision}f}"


def _build_snapshot_metrics_json(snapshot: AdSnapshot | None) -> dict[str, object]:
    """Собирает текущие метрики рекомендации из актуального snapshot."""
    if snapshot is None:
        return {}
    return {
        "spend": _serialize_optional_decimal(getattr(snapshot, "spend", None), 2) or "0.00",
        "budget": str(getattr(snapshot, "budget", "") or "").strip() or None,
        "reach": int(getattr(snapshot, "reach", 0) or 0),
        "impressions": int(getattr(snapshot, "impressions", 0) or 0),
        "clicks": int(getattr(snapshot, "clicks", 0) or 0),
        "cpc": _serialize_optional_decimal(getattr(snapshot, "cpc", None), 4),
        "ctr": _serialize_optional_decimal(getattr(snapshot, "ctr", None), 4),
        "cost_per_result": _serialize_optional_decimal(
            getattr(snapshot, "cost_per_result", None), 4
        ),
        "cpm": _serialize_optional_decimal(getattr(snapshot, "cpm", None), 4),
        "frequency": _serialize_optional_decimal(getattr(snapshot, "frequency", None), 4),
        "leads": int(getattr(snapshot, "leads", 0) or 0),
        "cost_per_lead": _serialize_optional_decimal(getattr(snapshot, "cost_per_lead", None), 4),
        "registrations": int(getattr(snapshot, "registrations", 0) or 0),
        "cost_per_registration": _serialize_optional_decimal(
            getattr(snapshot, "cost_per_registration", None),
            4,
        ),
        "deposits": int(getattr(snapshot, "deposits", 0) or 0),
    }


def _incident_key_for_snapshot(snapshot: AdSnapshot) -> str:
    """Возвращает ключ текущего инцидента для snapshot."""
    return snapshot.open_state_token or snapshot.telegram_group_key or snapshot.fb_ad_id


def _matched_rule_codes_for_snapshot(snapshot: AdSnapshot) -> list[str]:
    """Возвращает набор правил для текущей стадии snapshot."""
    if snapshot.current_stage == AlertStage.EARLY_SIGNAL:
        return list(snapshot.early_signal_rule_codes or [])
    if snapshot.current_stage == AlertStage.WARNING:
        return list(snapshot.warning_rule_codes or [])
    return list(snapshot.stop_rule_codes or [])


def _disable_task_activity_at(task: DisableTask) -> datetime:
    """Возвращает момент последней активности disable-задачи."""
    return task.updated_at or task.completed_at or task.created_at


def _max_datetime(*values: datetime | None) -> datetime | None:
    """Возвращает максимальную непустую дату."""
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return max(filtered)


def _min_datetime(*values: datetime | None) -> datetime | None:
    """Возвращает минимальную непустую дату."""
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return min(filtered)


def _serialize_disable_task(task: DisableTask) -> DisableTaskSchema:
    """Сериализует DisableTask для API-ответа."""
    incident_key = getattr(task, "open_state_token", "") or getattr(task, "fb_ad_id", "")
    updated_at = getattr(task, "updated_at", None) or getattr(task, "created_at", None)
    return DisableTaskSchema(
        id=str(task.id),
        incident_key=incident_key,
        fb_ad_id=task.fb_ad_id,
        ad_name=task.ad_name,
        status=task.status.value,
        attempt_count=task.attempt_count,
        last_error=task.last_error,
        next_retry_at=_serialize_optional_datetime(task.next_retry_at),
        requested_by_username=task.requested_by_username,
        created_at=task.created_at.isoformat(),
        updated_at=updated_at.isoformat() if updated_at else task.created_at.isoformat(),
        completed_at=_serialize_optional_datetime(task.completed_at),
    )


def _build_active_incident_schema(
    snapshot: AdSnapshot,
    *,
    alert_events: list[AlertEvent],
    disable_tasks: list[DisableTask],
) -> ActiveIncidentSchema:
    """Собирает API-представление текущего инцидента по snapshot и связанным данным."""
    incident_key = _incident_key_for_snapshot(snapshot)
    current_events = [event for event in alert_events if event.telegram_group_key == incident_key]
    current_tasks = [task for task in disable_tasks if task.open_state_token == incident_key]

    latest_event = max(current_events, key=lambda event: event.created_at, default=None)
    latest_task = max(current_tasks, key=_disable_task_activity_at, default=None)
    has_active_disable_task = any(
        task.status
        in (
            DisableTaskStatus.PENDING,
            DisableTaskStatus.RUNNING,
            DisableTaskStatus.RETRYING,
        )
        for task in current_tasks
    )
    auto_attempts = sum(
        1 for task in current_tasks if (task.requested_by_username or "") == "bot_auto_stop"
    )
    incident_retry_count = max(auto_attempts - 1, 0)
    needs_manual_attention = (
        snapshot.alert_state == AlertState.CLAIMED
        and snapshot.current_stage == AlertStage.STOP
        and not is_delivery_disabled(snapshot.delivery_status)
        and not has_active_disable_task
        and incident_retry_count >= SILENT_DISABLE_INCIDENT_RETRY_LIMIT
    )
    latest_activity_at = (
        _max_datetime(
            snapshot.updated_at,
            snapshot.last_observed_at,
            latest_event.created_at if latest_event else None,
            _disable_task_activity_at(latest_task) if latest_task else None,
        )
        or snapshot.updated_at
    )
    started_at = _min_datetime(
        min((event.created_at for event in current_events), default=None),
        min((task.created_at for task in current_tasks), default=None),
        snapshot.created_at,
    )

    return ActiveIncidentSchema(
        incident_key=incident_key,
        fb_ad_id=snapshot.fb_ad_id,
        ad_name=snapshot.ad_name,
        campaign_name=snapshot.campaign_name,
        adset_name=snapshot.adset_name,
        current_state=snapshot.alert_state.value,
        current_stage=snapshot.current_stage.value if snapshot.current_stage else None,
        delivery_status=snapshot.delivery_status,
        matched_rule_codes=_matched_rule_codes_for_snapshot(snapshot),
        reason_title=latest_event.reason_title if latest_event else None,
        reason_text=latest_event.reason_text if latest_event else None,
        metrics_json=latest_event.metrics_json
        if latest_event and latest_event.metrics_json
        else {},
        started_at=_serialize_optional_datetime(started_at),
        last_activity_at=latest_activity_at.isoformat(),
        last_observed_at=_serialize_optional_datetime(snapshot.last_observed_at),
        latest_alert_at=_serialize_optional_datetime(
            latest_event.created_at if latest_event else None
        ),
        latest_alert_stage=latest_event.stage.value
        if latest_event and latest_event.stage
        else None,
        latest_disable_task_status=(latest_task.status.value if latest_task else None),
        latest_disable_task_created_at=(
            _serialize_optional_datetime(latest_task.created_at) if latest_task else None
        ),
        latest_disable_task_updated_at=(
            _serialize_optional_datetime(latest_task.updated_at) if latest_task else None
        ),
        latest_disable_task_attempt=(latest_task.attempt_count if latest_task else None),
        latest_disable_task_id=(str(latest_task.id) if latest_task else None),
        latest_disable_task_last_error=(latest_task.last_error if latest_task else None),
        latest_disable_task_next_retry_at=(
            _serialize_optional_datetime(latest_task.next_retry_at) if latest_task else None
        ),
        latest_disable_task_completed_at=(
            _serialize_optional_datetime(latest_task.completed_at) if latest_task else None
        ),
        waiting_for_off=(
            snapshot.alert_state == AlertState.CLAIMED
            and not is_delivery_disabled(snapshot.delivery_status)
        ),
        has_active_disable_task=has_active_disable_task,
        incident_retry_count=incident_retry_count,
        needs_manual_attention=needs_manual_attention,
    )


def _serialize_enable_task(task: EnableTask) -> EnableTaskSchema:
    """Сериализует EnableTask для API-ответов."""
    updated_at = getattr(task, "updated_at", None) or task.created_at
    last_error = task.last_error
    next_retry_at = task.next_retry_at
    if task.status == EnableTaskStatus.SUCCEEDED:
        last_error = None
        next_retry_at = None
    return EnableTaskSchema(
        id=str(task.id),
        recommendation_event_id=(
            str(task.recommendation_event_id) if task.recommendation_event_id else None
        ),
        fb_ad_id=task.fb_ad_id,
        ad_name=task.ad_name,
        status=task.status.value,
        attempt_count=task.attempt_count,
        last_error=last_error,
        next_retry_at=next_retry_at.isoformat() if next_retry_at else None,
        requested_by_username=task.requested_by_username,
        created_at=task.created_at.isoformat(),
        updated_at=updated_at.isoformat(),
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


def _build_current_enable_tasks_query(
    *,
    created_since: datetime | None = None,
):
    """Строит запрос только по актуальной задаче на каждое объявление."""
    ranked_tasks = select(
        EnableTask.id.label("task_id"),
        func.row_number()
        .over(
            partition_by=EnableTask.fb_ad_id,
            order_by=[
                EnableTask.updated_at.desc(),
                EnableTask.created_at.desc(),
                EnableTask.id.desc(),
            ],
        )
        .label("row_num"),
    ).subquery()

    query = (
        select(EnableTask)
        .join(ranked_tasks, ranked_tasks.c.task_id == EnableTask.id)
        .join(
            EnableRecommendationEvent,
            EnableRecommendationEvent.id == EnableTask.recommendation_event_id,
            isouter=True,
        )
        .where(
            ranked_tasks.c.row_num == 1,
            EnableTask.status.in_(
                [
                    EnableTaskStatus.PENDING,
                    EnableTaskStatus.RUNNING,
                    EnableTaskStatus.RETRYING,
                    EnableTaskStatus.FAILED,
                    EnableTaskStatus.SUCCEEDED,
                ]
            ),
        )
    )
    if created_since is not None:
        query = query.where(
            or_(
                EnableRecommendationEvent.live_batch_started_at >= created_since,
                and_(
                    EnableRecommendationEvent.id.is_(None),
                    EnableTask.created_at >= created_since,
                ),
            )
        )
    return query.order_by(EnableTask.updated_at.desc(), EnableTask.created_at.desc())


def _serialize_enable_recommendation_event(
    event: EnableRecommendationEvent,
    *,
    current_batch_marker: datetime | None,
    related_task: EnableTask | None = None,
    current_snapshot: AdSnapshot | None = None,
    live_candidate: EnableRecommendationCandidate | None = None,
) -> EnableRecommendationEventSchema:
    """Сериализует recommendation event для dashboard."""
    state = "OPEN"
    if related_task is not None:
        state = "TASK_CREATED"
    elif current_batch_marker is None or event.live_batch_started_at != current_batch_marker:
        state = "STALE"

    recommendation_level = (
        live_candidate.recommendation_level
        if live_candidate is not None
        else event.recommendation_level
    )
    reason_title = live_candidate.reason_title if live_candidate is not None else event.reason_title
    reason_text = live_candidate.reason_text if live_candidate is not None else event.reason_text
    reason_title, reason_text = _normalize_enable_recommendation_reason(
        recommendation_level=recommendation_level,
        reason_title=reason_title,
        reason_text=reason_text,
    )
    metrics_json = (
        dict(live_candidate.metrics_json)
        if live_candidate is not None
        else _build_snapshot_metrics_json(current_snapshot)
        if current_snapshot
        else dict(event.metrics_json or {})
    )
    rule_summaries_source = (
        live_candidate.metrics_json if live_candidate is not None else (event.metrics_json or {})
    )
    rule_summaries = rule_summaries_source.get("rule_summaries")
    if isinstance(rule_summaries, list) and rule_summaries:
        metrics_json["rule_summaries"] = rule_summaries
    updated_at = getattr(event, "updated_at", None)
    if current_snapshot is not None and (
        updated_at is None or current_snapshot.last_observed_at > updated_at
    ):
        updated_at = current_snapshot.last_observed_at

    return EnableRecommendationEventSchema(
        id=str(event.id),
        fb_ad_id=event.fb_ad_id,
        ad_name=current_snapshot.ad_name if current_snapshot else event.ad_name,
        campaign_name=current_snapshot.campaign_name if current_snapshot else None,
        adset_name=current_snapshot.adset_name if current_snapshot else None,
        delivery_status=current_snapshot.delivery_status
        if current_snapshot
        else event.delivery_status,
        recommendation_level=recommendation_level.value,
        matched_rule_codes=(
            list(live_candidate.matched_rule_codes)
            if live_candidate is not None
            else event.matched_rule_codes or []
        ),
        reason_title=reason_title,
        reason_text=reason_text,
        metrics_json=metrics_json,
        live_batch_started_at=event.live_batch_started_at.isoformat(),
        created_at=event.created_at.isoformat(),
        updated_at=_serialize_optional_datetime(updated_at),
        state=state,
        related_enable_task_id=str(related_task.id) if related_task else None,
        related_enable_task_status=related_task.status.value if related_task else None,
    )


async def _load_ad_snapshots_by_fb_ad_id(
    db: AsyncSession,
    *,
    fb_ad_ids: list[str],
) -> dict[str, AdSnapshot]:
    """Загружает текущие snapshot по fb_ad_id."""
    if not fb_ad_ids:
        return {}

    result = await db.execute(select(AdSnapshot).where(AdSnapshot.fb_ad_id.in_(fb_ad_ids)))
    return {snapshot.fb_ad_id: snapshot for snapshot in result.scalars().all()}


async def _load_current_enable_recommendations(
    db: AsyncSession,
    *,
    limit: int | None = None,
) -> tuple[datetime | None, list[CurrentEnableRecommendationRow]]:
    """Загружает текущие рекомендации, подтверждённые live-переоценкой snapshot."""
    last_scan, batch_start = await load_live_batch_bounds(db)
    if last_scan is None or batch_start is None:
        return None, []

    current_batch_marker = compute_live_batch_marker(last_scan)
    result = await db.execute(
        select(EnableRecommendationEvent)
        .where(EnableRecommendationEvent.live_batch_started_at == current_batch_marker)
        .order_by(
            func.coalesce(
                EnableRecommendationEvent.updated_at, EnableRecommendationEvent.created_at
            ).desc(),
            EnableRecommendationEvent.created_at.desc(),
        )
    )
    events = result.scalars().all()
    snapshot_by_ad = await _load_ad_snapshots_by_fb_ad_id(
        db,
        fb_ad_ids=[event.fb_ad_id for event in events],
    )
    live_snapshots = [
        snapshot
        for snapshot in snapshot_by_ad.values()
        if is_within_live_batch(snapshot.last_observed_at, batch_start)
        and snapshot.delivery_status in RECOMMENDATION_DELIVERY_STATUSES
    ]
    live_candidates = await collect_enable_recommendation_candidates_for_snapshots(
        db,
        snapshots=live_snapshots,
        live_batch_started_at=current_batch_marker,
    )
    candidate_by_ad = {candidate.fb_ad_id: candidate for candidate in live_candidates}
    latest_by_ad: dict[str, CurrentEnableRecommendationRow] = {}
    for event in events:
        snapshot = snapshot_by_ad.get(event.fb_ad_id)
        if snapshot is None:
            continue
        if not is_within_live_batch(snapshot.last_observed_at, batch_start):
            continue
        if snapshot.delivery_status not in RECOMMENDATION_DELIVERY_STATUSES:
            continue
        candidate = candidate_by_ad.get(event.fb_ad_id)
        if candidate is None:
            continue
        if event.fb_ad_id not in latest_by_ad:
            latest_by_ad[event.fb_ad_id] = CurrentEnableRecommendationRow(
                event=event,
                snapshot=snapshot,
                candidate=candidate,
            )

    rows = list(latest_by_ad.values())
    if limit is not None:
        rows = rows[:limit]
    return current_batch_marker, rows


def _safe_decimal_div(numerator: Decimal, denominator: int) -> Decimal | None:
    """Безопасно делит Decimal на целое число для cost-метрик."""
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def _safe_percent(numerator: int, denominator: int) -> float | None:
    """Возвращает конверсию в процентах или None, если делить нельзя."""
    if denominator <= 0:
        return None
    return round((float(numerator) / float(denominator)) * 100, 1)


def _safe_decimal_percent_over(value: Decimal, baseline: Decimal) -> Decimal | None:
    """Возвращает процент превышения над базовым порогом или None."""
    baseline_decimal = Decimal(baseline)
    if baseline_decimal <= 0:
        return None
    ratio = Decimal(value) / baseline_decimal
    if ratio <= 1:
        return None
    return (ratio - Decimal("1")) * Decimal("100")


def _safe_decimal_percent_delta(value: Decimal, baseline: Decimal) -> Decimal | None:
    """Возвращает отклонение от базового порога со знаком."""
    baseline_decimal = Decimal(baseline)
    if baseline_decimal <= 0:
        return None
    ratio = Decimal(value) / baseline_decimal
    return (ratio - Decimal("1")) * Decimal("100")


def _percent_of_cpa(cpa_amount: Decimal, percent: Decimal) -> Decimal:
    """Возвращает абсолютный порог как процент от CPA."""
    return (Decimal(cpa_amount) * Decimal(percent)) / Decimal("100")


def _build_snapshot_base_budget_reference(
    snapshot: AdSnapshot,
    *,
    cpa_amount: Decimal,
    rule_config: OfferRuleConfig,
) -> dict[str, object] | None:
    """Возвращает базовый бюджет объявления по самой глубокой доступной стадии."""
    clicks = int(snapshot.clicks or 0)
    leads = int(snapshot.leads or 0)
    registrations = int(snapshot.registrations or 0)
    deposits = int(snapshot.deposits or 0)
    spend = Decimal(snapshot.spend or 0)
    cpa_decimal = Decimal(cpa_amount)

    cpc_budget = (
        _percent_of_cpa(cpa_decimal, Decimal(rule_config.cpc_percent_stop))
        if rule_config.cpc_percent_enabled
        else None
    )
    cpl_budget = (
        _percent_of_cpa(cpa_decimal, Decimal(rule_config.cpl_percent_stop))
        if rule_config.cpl_percent_enabled
        else None
    )
    cpr_budget = (
        _percent_of_cpa(cpa_decimal, Decimal(rule_config.cpr_percent_stop))
        if rule_config.cpr_percent_enabled
        else None
    )

    label: str | None = None
    ideal_spend: Decimal | None = None

    if deposits >= 1 and registrations >= 1 and cpa_decimal > 0:
        label = "CPA"
        ideal_spend = cpa_decimal * Decimal(deposits)
    elif registrations >= 1:
        if cpr_budget is not None and cpr_budget > 0:
            label = "CPR"
            ideal_spend = cpr_budget * Decimal(registrations)
        elif rule_config.spend_no_dep_enabled:
            label = "Расход без депозита"
            ideal_spend = _percent_of_cpa(
                cpa_decimal, Decimal(rule_config.spend_no_dep_from_percent)
            )
    elif leads >= 1:
        if cpl_budget is not None and cpl_budget > 0:
            label = "CPL"
            ideal_spend = cpl_budget * Decimal(leads)
        elif cpr_budget is not None and cpr_budget > 0:
            label = "Расход до регистрации"
            ideal_spend = cpr_budget
    elif clicks >= 1:
        if cpc_budget is not None and cpc_budget > 0:
            label = "CPC"
            ideal_spend = cpc_budget * Decimal(clicks)
        elif cpl_budget is not None and cpl_budget > 0:
            label = "Расход до лида"
            ideal_spend = cpl_budget
    elif cpc_budget is not None and cpc_budget > 0:
        label = "Расход до клика"
        ideal_spend = cpc_budget

    if label is None or ideal_spend is None or ideal_spend <= 0:
        return None

    overrun_amount = spend - ideal_spend
    overrun_percent = _safe_decimal_percent_over(spend, ideal_spend)
    return {
        "label": label,
        "actual_spend": spend,
        "ideal_spend": ideal_spend,
        "overrun_amount": overrun_amount,
        "overrun_percent": overrun_percent,
    }


async def _load_offer_rules_for_snapshots(
    db: AsyncSession,
    snapshots: list[AdSnapshot],
) -> dict[_uuid.UUID, tuple[Offer, OfferRuleConfig]]:
    """Загружает offer + rule config для списка snapshot с offer_id."""
    offer_ids = {snapshot.offer_id for snapshot in snapshots if snapshot.offer_id is not None}
    if not offer_ids:
        return {}

    result = await db.execute(
        select(Offer, OfferRuleConfig)
        .join(OfferRuleConfig, OfferRuleConfig.offer_id == Offer.id)
        .where(Offer.id.in_(offer_ids))
    )
    return {offer.id: (offer, rule_config) for offer, rule_config in result.all()}


def _build_campaign_stop_overrun_rows(
    snapshots: list[AdSnapshot],
    offer_rule_map: dict[_uuid.UUID, tuple[Offer, OfferRuleConfig]],
) -> list[dict]:
    """Возвращает отклонение от базовой экономики в разрезе кампаний."""
    grouped: dict[str, dict[str, object]] = {}

    for snapshot in snapshots:
        if not snapshot.campaign_name or snapshot.offer_id is None:
            continue
        offer_bundle = offer_rule_map.get(snapshot.offer_id)
        if offer_bundle is None:
            continue

        offer, rule_config = offer_bundle
        budget_reference = _build_snapshot_base_budget_reference(
            snapshot,
            cpa_amount=Decimal(offer.cpa_amount),
            rule_config=rule_config,
        )
        if budget_reference is None:
            continue

        campaign_name = snapshot.campaign_name
        actual_spend = Decimal(budget_reference["actual_spend"])
        ideal_spend = Decimal(budget_reference["ideal_spend"])
        overrun_amount = Decimal(budget_reference["overrun_amount"])
        overrun_percent = budget_reference["overrun_percent"]
        bucket = grouped.setdefault(
            campaign_name,
            {
                "campaign": campaign_name[:30] + "…" if len(campaign_name) > 30 else campaign_name,
                "campaign_full": campaign_name,
                "actual_spend_sum": Decimal("0"),
                "ideal_spend_sum": Decimal("0"),
                "total_ads": 0,
                "affected_ads": 0,
                "over_budget_ads": 0,
                "under_budget_ads": 0,
                "on_target_ads": 0,
                "dominant_metric": None,
                "top_ad_name": None,
                "max_ad_overrun_amount": Decimal("0"),
                "max_ad_overrun_percent": Decimal("0"),
            },
        )
        bucket["total_ads"] = int(bucket["total_ads"]) + 1
        bucket["actual_spend_sum"] = Decimal(bucket["actual_spend_sum"]) + actual_spend
        bucket["ideal_spend_sum"] = Decimal(bucket["ideal_spend_sum"]) + ideal_spend

        if overrun_amount > 0:
            bucket["affected_ads"] = int(bucket["affected_ads"]) + 1
            bucket["over_budget_ads"] = int(bucket["over_budget_ads"]) + 1
        elif overrun_amount < 0:
            bucket["under_budget_ads"] = int(bucket["under_budget_ads"]) + 1
        else:
            bucket["on_target_ads"] = int(bucket["on_target_ads"]) + 1

        if overrun_amount > Decimal(bucket["max_ad_overrun_amount"]):
            bucket["max_ad_overrun_amount"] = overrun_amount
            bucket["max_ad_overrun_percent"] = (
                Decimal(overrun_percent) if overrun_percent is not None else Decimal("0")
            )
            bucket["dominant_metric"] = budget_reference["label"]
            bucket["top_ad_name"] = snapshot.ad_name

    rows: list[dict[str, object]] = []
    for item in grouped.values():
        ideal_spend = Decimal(item["ideal_spend_sum"])
        actual_spend = Decimal(item["actual_spend_sum"])
        budget_delta_amount = actual_spend - ideal_spend
        budget_delta_percent = _safe_decimal_percent_delta(actual_spend, ideal_spend)
        if budget_delta_percent is None:
            continue
        if budget_delta_amount > 0:
            budget_status = "OVER"
        elif budget_delta_amount < 0:
            budget_status = "UNDER"
        else:
            budget_status = "ON_TARGET"
        rows.append(
            {
                **item,
                "actual_spend": actual_spend,
                "ideal_spend": ideal_spend,
                "budget_delta_amount": budget_delta_amount,
                "budget_delta_percent": budget_delta_percent,
                "budget_status": budget_status,
                "overrun_amount": budget_delta_amount,
                "overrun_percent": budget_delta_percent,
            }
        )

    rows = sorted(
        rows,
        key=lambda item: (
            0
            if str(item["budget_status"]) == "OVER"
            else 1
            if str(item["budget_status"]) == "UNDER"
            else 2,
            -abs(Decimal(item["budget_delta_percent"])),
            -abs(Decimal(item["budget_delta_amount"])),
            -int(item["over_budget_ads"]),
            str(item["campaign_full"]),
        ),
    )
    return [
        {
            "campaign": row["campaign"],
            "campaign_full": row["campaign_full"],
            "budget_delta_percent": round(float(Decimal(row["budget_delta_percent"])), 1),
            "budget_delta_amount": round(float(Decimal(row["budget_delta_amount"])), 2),
            "budget_status": row["budget_status"],
            "overrun_percent": round(float(Decimal(row["overrun_percent"])), 1),
            "actual_spend": round(float(Decimal(row["actual_spend"])), 2),
            "ideal_spend": round(float(Decimal(row["ideal_spend"])), 2),
            "overrun_amount": round(float(Decimal(row["overrun_amount"])), 2),
            "total_ads": int(row["total_ads"]),
            "affected_ads": int(row["affected_ads"]),
            "over_budget_ads": int(row["over_budget_ads"]),
            "under_budget_ads": int(row["under_budget_ads"]),
            "on_target_ads": int(row["on_target_ads"]),
            "dominant_metric": row["dominant_metric"],
            "top_ad_name": row["top_ad_name"],
            "max_ad_overrun_amount": round(float(Decimal(row["max_ad_overrun_amount"])), 2),
            "max_ad_overrun_percent": round(float(Decimal(row["max_ad_overrun_percent"])), 1),
        }
        for row in rows
    ]


@lru_cache(maxsize=1)
def _dashboard_timezone() -> ZoneInfo:
    """Часовой пояс dashboard для локальных суточных срезов."""
    return ZoneInfo(get_settings().app_timezone)


def _dashboard_now() -> datetime:
    """Текущее время в часовом поясе dashboard."""
    return datetime.now(_dashboard_timezone())


def _to_dashboard_timezone(value: datetime) -> datetime:
    """Переводит дату в локальный часовой пояс dashboard."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_dashboard_timezone())


async def _get_cabinet_day_start(db: AsyncSession) -> datetime | None:
    """Возвращает зафиксированное начало текущих суток кабинета."""
    row = await db.scalar(
        select(ObserverSettings.cabinet_day_started_at).where(
            ObserverSettings.singleton_key == "default"
        )
    )
    return row


def _serialize_observer_runtime_fields(
    row: ObserverSettings | None,
) -> dict[str, str | None]:
    """Сериализует runtime-статус observer для dashboard."""
    if row is None:
        return {
            "observer_status": None,
            "observer_status_message": None,
            "observer_heartbeat_at": None,
            "observer_last_error": None,
            "observer_last_error_at": None,
        }

    return {
        "observer_status": row.worker_status,
        "observer_status_message": row.worker_message,
        "observer_heartbeat_at": (
            row.worker_heartbeat_at.isoformat() if row.worker_heartbeat_at else None
        ),
        "observer_last_error": row.worker_last_error,
        "observer_last_error_at": (
            row.worker_last_error_at.isoformat() if row.worker_last_error_at else None
        ),
    }


def _timeline_bucket_start(value: datetime, period: str) -> datetime:
    """Нормализует время до начала бакета."""
    if period in {"7d", "30d"}:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.replace(minute=0, second=0, microsecond=0)


def _build_current_risk_reason_rows(snapshots: list[AdSnapshot]) -> list[dict[str, int | str]]:
    """Строит топ причин по текущим рискованным объявлениям."""
    from core.rules.labels import RULE_LABELS, RULE_LABELS_SHORT

    risk_labels = {
        code: (full, RULE_LABELS_SHORT.get(code, code)) for code, full in RULE_LABELS.items()
    }
    risk_counts: dict[str, int] = {}
    for snapshot in snapshots:
        if snapshot.alert_state == AlertState.EARLY_SIGNAL_SENT:
            matched_codes = snapshot.early_signal_rule_codes or []
        elif snapshot.alert_state == AlertState.WARNING_SENT:
            matched_codes = snapshot.warning_rule_codes or []
        elif snapshot.alert_state in (AlertState.STOP_SENT, AlertState.CLAIMED):
            matched_codes = snapshot.stop_rule_codes or []
        else:
            matched_codes = []

        for code in set(matched_codes):
            risk_counts[code] = risk_counts.get(code, 0) + 1

    return sorted(
        [
            {
                "rule": risk_labels.get(code, (code, code))[0],
                "rule_short": risk_labels.get(code, (code, code))[1],
                "count": count,
            }
            for code, count in risk_counts.items()
        ],
        key=lambda item: (-int(item["count"]), str(item["rule"])),
    )


def _timeline_bucket_step(period: str) -> timedelta:
    """Шаг бакета для таймлайна."""
    return timedelta(days=1) if period in {"7d", "30d"} else timedelta(hours=1)


def _timeline_bucket_label(value: datetime, period: str) -> str:
    """Подпись бакета для UI."""
    return value.strftime("%d.%m") if period in {"7d", "30d"} else value.strftime("%H:00")


def _build_performance_summary(
    *,
    spend: Decimal,
    clicks: int,
    leads: int,
    registrations: int,
    deposits: int,
    offers: list[Offer] | None = None,
) -> DashboardPerformanceSummarySchema:
    """Собирает сводный блок performance-метрик."""
    offers = offers or []

    # Расчет ROAS: (deposits × средний cpa_amount офферов) / spend
    roas = None
    if offers and deposits > 0 and spend > 0:
        total_cpa = sum(float(offer.cpa_amount or 0) for offer in offers)
        avg_cpa = total_cpa / len(offers)
        if avg_cpa > 0:
            revenue = Decimal(str(deposits)) * Decimal(str(avg_cpa))
            roas = revenue / spend

    return DashboardPerformanceSummarySchema(
        spend=spend,
        clicks=clicks,
        leads=leads,
        registrations=registrations,
        deposits=deposits,
        cpc=_safe_decimal_div(spend, clicks),
        cpl=_safe_decimal_div(spend, leads),
        cpr=_safe_decimal_div(spend, registrations),
        spend_per_dep=_safe_decimal_div(spend, deposits),
        roas=roas,
        click_to_lead_rate=_safe_percent(leads, clicks),
        lead_to_reg_rate=_safe_percent(registrations, leads),
        reg_to_dep_rate=_safe_percent(deposits, registrations),
    )


def _json_decimal(value: object | None) -> Decimal:
    """Преобразует число из JSON/ORM в Decimal без падения."""
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _json_int(value: object | None) -> int:
    """Преобразует число из JSON/ORM в int без падения."""
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except Exception:
        return 0


def _accumulate_campaign_metrics(
    campaign_map: dict[str, dict[str, object]],
    *,
    campaign_name: str,
    spend: Decimal,
    clicks: int,
    leads: int,
    registrations: int,
    deposits: int,
) -> None:
    """Накапливает агрегаты по кампании из разных источников истории."""
    if not campaign_name:
        return
    row = campaign_map.setdefault(
        campaign_name,
        {
            "campaign": campaign_name,
            "spend": Decimal("0"),
            "clicks": 0,
            "leads": 0,
            "registrations": 0,
            "deposits": 0,
        },
    )
    row["spend"] += spend
    row["clicks"] += clicks
    row["leads"] += leads
    row["registrations"] += registrations
    row["deposits"] += deposits


def _finalize_campaign_rows(
    campaign_map: dict[str, dict[str, object]],
) -> list[DashboardPerformanceCampaignSchema]:
    """Преобразует накопленную карту кампаний в схемы API."""
    return [
        DashboardPerformanceCampaignSchema(
            campaign=str(row["campaign"]),
            spend=Decimal(row["spend"]),
            clicks=int(row["clicks"]),
            leads=int(row["leads"]),
            registrations=int(row["registrations"]),
            deposits=int(row["deposits"]),
            cpc=_safe_decimal_div(Decimal(row["spend"]), int(row["clicks"])),
            cpl=_safe_decimal_div(Decimal(row["spend"]), int(row["leads"])),
            cpr=_safe_decimal_div(Decimal(row["spend"]), int(row["registrations"])),
            spend_per_dep=_safe_decimal_div(Decimal(row["spend"]), int(row["deposits"])),
            click_to_lead_rate=_safe_percent(int(row["leads"]), int(row["clicks"])),
            lead_to_reg_rate=_safe_percent(int(row["registrations"]), int(row["leads"])),
            reg_to_dep_rate=_safe_percent(int(row["deposits"]), int(row["registrations"])),
        )
        for row in sorted(campaign_map.values(), key=lambda item: item["spend"], reverse=True)
    ]


def _build_dashboard_performance_payload(
    snapshots: list[AdSnapshot],
    *,
    offers: list[Offer] | None = None,
    period: str,
    now: datetime | None = None,
    cutoff: datetime | None = None,
    archives: list[CabinetDayArchive] | None = None,
) -> DashboardPerformanceSchema:
    """Агрегирует performance-данные из текущего дня и архива суток кабинета."""
    current_time = now or _dashboard_now()
    cutoff = cutoff or _performance_cutoff(period, current_time)
    archives = archives or []
    offers = offers or []
    relevant = [
        snapshot
        for snapshot in snapshots
        if snapshot.last_observed_at and _to_dashboard_timezone(snapshot.last_observed_at) >= cutoff
    ]

    step = _timeline_bucket_step(period)
    bucket_cursor = _timeline_bucket_start(cutoff, period)
    last_bucket = _timeline_bucket_start(current_time, period)
    timeline_map: dict[datetime, dict] = {}
    while bucket_cursor <= last_bucket:
        timeline_map[bucket_cursor] = {
            "timestamp": bucket_cursor.isoformat(),
            "label": _timeline_bucket_label(bucket_cursor, period),
            "spend": Decimal("0"),
            "registrations": 0,
            "deposits": 0,
        }
        bucket_cursor += step

    total_spend = Decimal("0")
    total_clicks = 0
    total_leads = 0
    total_regs = 0
    total_deps = 0
    campaign_map: dict[str, dict[str, object]] = {}

    for archive in archives:
        summary = archive.summary_json or {}
        spend = _json_decimal(summary.get("spend"))
        clicks = _json_int(summary.get("clicks"))
        leads = _json_int(summary.get("leads"))
        registrations = _json_int(summary.get("registrations"))
        deposits = _json_int(summary.get("deposits"))

        total_spend += spend
        total_clicks += clicks
        total_leads += leads
        total_regs += registrations
        total_deps += deposits

        bucket_source = archive.started_at or archive.ended_at or archive.reset_detected_at
        if bucket_source is not None:
            bucket = _timeline_bucket_start(_to_dashboard_timezone(bucket_source), period)
            if bucket in timeline_map:
                timeline_map[bucket]["spend"] += spend
                timeline_map[bucket]["registrations"] += registrations
                timeline_map[bucket]["deposits"] += deposits

        for row in archive.campaigns_json or []:
            _accumulate_campaign_metrics(
                campaign_map,
                campaign_name=str(row.get("campaign") or "").strip(),
                spend=_json_decimal(row.get("spend")),
                clicks=_json_int(row.get("clicks")),
                leads=_json_int(row.get("leads")),
                registrations=_json_int(row.get("registrations")),
                deposits=_json_int(row.get("deposits")),
            )

    for snapshot in relevant:
        spend = Decimal(snapshot.spend or 0)
        clicks = int(snapshot.clicks or 0)
        leads = int(snapshot.leads or 0)
        registrations = int(snapshot.registrations or 0)
        deposits = int(snapshot.deposits or 0)

        total_spend += spend
        total_clicks += clicks
        total_leads += leads
        total_regs += registrations
        total_deps += deposits

        bucket = _timeline_bucket_start(_to_dashboard_timezone(snapshot.last_observed_at), period)
        if bucket in timeline_map:
            timeline_map[bucket]["spend"] += spend
            timeline_map[bucket]["registrations"] += registrations
            timeline_map[bucket]["deposits"] += deposits

        campaign_name = (snapshot.campaign_name or "").strip()
        _accumulate_campaign_metrics(
            campaign_map,
            campaign_name=campaign_name,
            spend=spend,
            clicks=clicks,
            leads=leads,
            registrations=registrations,
            deposits=deposits,
        )

    funnel = [
        DashboardPerformanceFunnelStepSchema(key="clicks", label="Клики", count=total_clicks),
        DashboardPerformanceFunnelStepSchema(
            key="leads",
            label="Лиды",
            count=total_leads,
            conversion_rate=_safe_percent(total_leads, total_clicks),
        ),
        DashboardPerformanceFunnelStepSchema(
            key="registrations",
            label="Реги",
            count=total_regs,
            conversion_rate=_safe_percent(total_regs, total_leads),
        ),
        DashboardPerformanceFunnelStepSchema(
            key="deposits",
            label="Депозиты",
            count=total_deps,
            conversion_rate=_safe_percent(total_deps, total_regs),
        ),
    ]

    campaigns = _finalize_campaign_rows(campaign_map)
    timeline = [
        DashboardPerformanceTimelinePointSchema(**row)
        for _, row in sorted(timeline_map.items(), key=lambda item: item[0])
    ]

    return DashboardPerformanceSchema(
        period=period,
        summary=_build_performance_summary(
            spend=total_spend,
            clicks=total_clicks,
            leads=total_leads,
            registrations=total_regs,
            deposits=total_deps,
            offers=offers,
        ),
        funnel=funnel,
        timeline=timeline,
        campaigns=campaigns,
    )


async def _resolve_dashboard_snapshot_cutoff(
    db: AsyncSession,
) -> datetime:
    """Возвращает границу актуальной скан-сессии для текущих snapshot-ов."""
    last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
    return _current_scan_cutoff(last_scan)


async def _resolve_dashboard_event_cutoff(
    db: AsyncSession,
    *,
    period: str,
    now: datetime,
) -> datetime:
    """Возвращает границу периода для событий и rule-history."""
    if period != "today":
        return _performance_cutoff(period, now)
    cabinet_day_start = await _get_cabinet_day_start(db)
    if cabinet_day_start is not None:
        return cabinet_day_start
    last_archive_end = await db.scalar(select(func.max(CabinetDayArchive.ended_at)))
    if last_archive_end is not None:
        return last_archive_end
    # Временный fallback, пока observer ещё не зафиксировал zero-scan для новых суток.
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _load_dashboard_archives(
    db: AsyncSession,
    *,
    cutoff: datetime,
) -> list[CabinetDayArchive]:
    """Загружает архивы завершённых суток кабинета, попадающие в период."""
    result = await db.execute(
        select(CabinetDayArchive)
        .where(CabinetDayArchive.ended_at >= cutoff)
        .order_by(CabinetDayArchive.started_at.asc())
    )
    return result.scalars().all()


async def _load_frequency_thresholds_by_offer(
    db: AsyncSession,
    *,
    offer_codes: set[str],
) -> dict[str, tuple[Decimal, Decimal]]:
    """Загружает пороги Frequency по кодам офферов."""
    normalized_codes = {
        _offer_code_lookup_key(code) for code in offer_codes if _offer_code_lookup_key(code)
    }
    if not normalized_codes:
        return {}

    result = await db.execute(
        select(
            Offer.code,
            OfferRuleConfig.frequency_elevated_threshold,
            OfferRuleConfig.frequency_critical_threshold,
        )
        .join(OfferRuleConfig, OfferRuleConfig.offer_id == Offer.id)
        .where(func.lower(Offer.code).in_(normalized_codes))
    )
    return {
        code.casefold(): (Decimal(elevated), Decimal(critical))
        for code, elevated, critical in result.all()
    }


async def _build_snapshot_diagnostics_map(
    db: AsyncSession,
    snapshots: list[AdSnapshot],
) -> dict[str, AdDiagnosticsSchema]:
    """Строит диагностику CPM/Frequency для набора снэпшотов."""
    if not snapshots:
        return {}

    scan_cutoff = await _resolve_dashboard_snapshot_cutoff(db)
    active_result = await db.execute(
        select(AdSnapshot)
        .where(
            AdSnapshot.last_observed_at >= scan_cutoff,
            AdSnapshot.delivery_status != "OFF",
        )
        .order_by(AdSnapshot.last_observed_at.desc())
    )
    active_snapshots = active_result.scalars().all()
    cpm_baselines = compute_cpm_baselines_by_offer(
        [snapshot for snapshot in active_snapshots if snapshot.resolved_offer_code],
        offer_code_getter=lambda snapshot: _offer_code_lookup_key(snapshot.resolved_offer_code),
        cpm_getter=lambda snapshot: snapshot.cpm,
    )
    frequency_thresholds = await _load_frequency_thresholds_by_offer(
        db,
        offer_codes={
            snapshot.resolved_offer_code for snapshot in snapshots if snapshot.resolved_offer_code
        },
    )

    diagnostics_map: dict[str, AdDiagnosticsSchema] = {}
    for snapshot in snapshots:
        offer_code_key = _offer_code_lookup_key(snapshot.resolved_offer_code)
        elevated_threshold, critical_threshold = frequency_thresholds.get(
            offer_code_key,
            (Decimal("2"), Decimal("3")),
        )
        diagnostics = build_ad_quality_diagnostics(
            cpm_value=snapshot.cpm,
            cpm_baseline=cpm_baselines.get(offer_code_key),
            frequency_value=snapshot.frequency,
            frequency_elevated_threshold=elevated_threshold,
            frequency_critical_threshold=critical_threshold,
        )
        diagnostics_map[snapshot.fb_ad_id] = AdDiagnosticsSchema(**diagnostics.as_dict())

    return diagnostics_map


# ==========================================
# Эндпоинты — Dashboard
# ==========================================


@router.get("/dashboard/stats", response_model=DashboardStatsSchema)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Сводная статистика для главной страницы dashboard.

    Все счётчики — только по объявлениям из текущей скан-сессии
    (виденным в течение 30 минут от последнего скана).
    """
    # Определяем границу текущей скан-сессии
    last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
    last_scan_str = last_scan.isoformat() if last_scan else None
    scan_cutoff = _current_scan_cutoff(last_scan)

    # Все счётчики — один GROUP BY только по текущей сессии
    state_stats = await db.execute(
        select(
            AdSnapshot.alert_state,
            func.count().label("cnt"),
            func.coalesce(func.sum(AdSnapshot.spend), 0).label("spend"),
        )
        .where(AdSnapshot.last_observed_at >= scan_cutoff)
        .group_by(AdSnapshot.alert_state)
    )
    rows = state_stats.all()

    total = 0
    early_signal = 0
    warning = 0
    stop = 0
    disabled = 0
    claimed = 0
    total_spend = Decimal("0")
    for state, cnt, spend in rows:
        total += cnt
        total_spend += spend or Decimal("0")
        if state == AlertState.EARLY_SIGNAL_SENT:
            early_signal = cnt
        elif state == AlertState.WARNING_SENT:
            warning = cnt
        elif state == AlertState.STOP_SENT:
            stop = cnt
        elif state == AlertState.DISABLED:
            disabled = cnt
        elif state == AlertState.CLAIMED:
            claimed = cnt

    # Активные офферы + задачи на отключение
    observer_row = await db.scalar(
        select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
    )
    cabinet_day_start = observer_row.cabinet_day_started_at if observer_row else None
    # Если zero-scan ещё ни разу не был зафиксирован, считаем только по актуальной скан-сессии,
    # чтобы не тащить вчерашние значения из календарной полуночи.
    disabled_since = cabinet_day_start or scan_cutoff
    active_offers = (
        await db.scalar(select(func.count()).select_from(Offer).where(Offer.is_active.is_(True)))
        or 0
    )
    pending_tasks = (
        await db.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(
                DisableTask.status.in_(
                    [
                        DisableTaskStatus.PENDING,
                        DisableTaskStatus.RETRYING,
                        DisableTaskStatus.RUNNING,
                    ]
                )
            )
        )
        or 0
    )
    pending_enable_tasks = (
        await db.scalar(
            (
                select(func.count())
                .select_from(EnableTask)
                .join(
                    EnableRecommendationEvent,
                    EnableRecommendationEvent.id == EnableTask.recommendation_event_id,
                    isouter=True,
                )
                .where(
                    EnableTask.status.in_(
                        [
                            EnableTaskStatus.PENDING,
                            EnableTaskStatus.RETRYING,
                            EnableTaskStatus.RUNNING,
                        ]
                    )
                )
                .where(
                    or_(
                        EnableRecommendationEvent.live_batch_started_at >= cabinet_day_start,
                        and_(
                            EnableRecommendationEvent.id.is_(None),
                            EnableTask.created_at >= cabinet_day_start,
                        ),
                    )
                )
            )
            if cabinet_day_start is not None
            else select(func.count())
            .select_from(EnableTask)
            .where(
                EnableTask.status.in_(
                    [
                        EnableTaskStatus.PENDING,
                        EnableTaskStatus.RETRYING,
                        EnableTaskStatus.RUNNING,
                    ]
                )
            )
        )
        or 0
    )
    disabled_today = (
        await db.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(
                DisableTask.status == DisableTaskStatus.SUCCEEDED,
                DisableTask.completed_at >= disabled_since,
            )
        )
        or 0
    )
    _, current_enable_recommendations = await _load_current_enable_recommendations(db)
    enable_recommendations_ok = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.OK
    )
    enable_recommendations_early_signal = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.EARLY_SIGNAL
    )
    enable_recommendations_warning = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.WARNING
    )

    return DashboardStatsSchema(
        total_ads_monitored=total,
        active_ads_count=total,
        ads_in_early_signal=early_signal,
        ads_in_warning=warning,
        ads_in_stop=stop,
        ads_disabled=disabled,
        ads_claimed=claimed,
        ads_disabled_today=disabled_today,
        total_spend=total_spend,
        active_offers=active_offers,
        pending_disable_tasks=pending_tasks,
        pending_enable_tasks=pending_enable_tasks,
        enable_recommendations_ok=enable_recommendations_ok,
        enable_recommendations_early_signal=enable_recommendations_early_signal,
        enable_recommendations_warning=enable_recommendations_warning,
        last_scan_at=last_scan_str,
        **_serialize_observer_runtime_fields(observer_row),
    )


@router.get("/dashboard/performance", response_model=DashboardPerformanceSchema)
async def get_dashboard_performance(
    period: str = Query("today", pattern="^(today|yesterday|7d|30d)$"),
    db: AsyncSession = Depends(get_db),
):
    """Performance-срез для гибридного dashboard."""
    now = _dashboard_now()
    snapshot_cutoff = await _resolve_dashboard_snapshot_cutoff(db)
    cutoff = snapshot_cutoff if period == "today" else _performance_cutoff(period, now)
    # Для yesterday: «сейчас» = полночь сегодня, чтобы timeline = только вчерашний день
    now_for_payload = (
        now.replace(hour=0, minute=0, second=0, microsecond=0) if period == "yesterday" else now
    )
    result = await db.execute(
        select(AdSnapshot)
        .where(AdSnapshot.last_observed_at >= snapshot_cutoff)
        .order_by(AdSnapshot.last_observed_at.asc())
    )
    snapshots = result.scalars().all()
    offer_result = await db.execute(select(Offer).where(Offer.is_active))
    offers = offer_result.scalars().all()
    archives = []
    if period != "today":
        archives = await _load_dashboard_archives(db, cutoff=cutoff)
    return _build_dashboard_performance_payload(
        snapshots,
        offers=offers,
        period=period,
        now=now_for_payload,
        cutoff=cutoff,
        archives=archives,
    )


@router.get("/dashboard/batch", response_model=DashboardBatchSchema)
async def get_dashboard_batch(
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """Один запрос вместо 4 для AdsPage.

    Возвращает ads, stats, incidents и disable-tasks одновременно.
    """
    # 1. Получить stats (полная логика из get_dashboard_stats)
    last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
    scan_cutoff = _current_scan_cutoff(last_scan)

    state_stats = await db.execute(
        select(
            AdSnapshot.alert_state,
            func.count().label("cnt"),
            func.coalesce(func.sum(AdSnapshot.spend), 0).label("spend"),
        )
        .where(AdSnapshot.last_observed_at >= scan_cutoff)
        .group_by(AdSnapshot.alert_state)
    )
    rows = state_stats.all()

    total = 0
    early_signal = 0
    warning = 0
    stop = 0
    disabled = 0
    claimed = 0
    total_spend = Decimal("0")
    for state, cnt, spend in rows:
        total += cnt
        total_spend += spend or Decimal("0")
        if state == AlertState.EARLY_SIGNAL_SENT:
            early_signal = cnt
        elif state == AlertState.WARNING_SENT:
            warning = cnt
        elif state == AlertState.STOP_SENT:
            stop = cnt
        elif state == AlertState.DISABLED:
            disabled = cnt
        elif state == AlertState.CLAIMED:
            claimed = cnt

    observer_row = await db.scalar(
        select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
    )
    cabinet_day_start = observer_row.cabinet_day_started_at if observer_row else None
    disabled_since = cabinet_day_start or scan_cutoff

    active_offers = (
        await db.scalar(select(func.count()).select_from(Offer).where(Offer.is_active.is_(True)))
        or 0
    )
    pending_tasks = (
        await db.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(
                DisableTask.status.in_(
                    [
                        DisableTaskStatus.PENDING,
                        DisableTaskStatus.RETRYING,
                        DisableTaskStatus.RUNNING,
                    ]
                )
            )
        )
        or 0
    )
    pending_enable_tasks = (
        await db.scalar(
            (
                select(func.count())
                .select_from(EnableTask)
                .join(
                    EnableRecommendationEvent,
                    EnableRecommendationEvent.id == EnableTask.recommendation_event_id,
                    isouter=True,
                )
                .where(
                    EnableTask.status.in_(
                        [
                            EnableTaskStatus.PENDING,
                            EnableTaskStatus.RETRYING,
                            EnableTaskStatus.RUNNING,
                        ]
                    )
                )
                .where(
                    or_(
                        EnableRecommendationEvent.live_batch_started_at >= cabinet_day_start,
                        and_(
                            EnableRecommendationEvent.id.is_(None),
                            EnableTask.created_at >= cabinet_day_start,
                        ),
                    )
                )
            )
            if cabinet_day_start is not None
            else select(func.count())
            .select_from(EnableTask)
            .where(
                EnableTask.status.in_(
                    [
                        EnableTaskStatus.PENDING,
                        EnableTaskStatus.RETRYING,
                        EnableTaskStatus.RUNNING,
                    ]
                )
            )
        )
        or 0
    )
    disabled_today = (
        await db.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(
                DisableTask.status == DisableTaskStatus.SUCCEEDED,
                DisableTask.completed_at >= disabled_since,
            )
        )
        or 0
    )
    _, current_enable_recommendations = await _load_current_enable_recommendations(db)
    enable_recommendations_ok = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.OK
    )
    enable_recommendations_early_signal = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.EARLY_SIGNAL
    )
    enable_recommendations_warning = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.WARNING
    )

    stats = DashboardStatsSchema(
        total_ads_monitored=total,
        active_ads_count=total,
        ads_in_early_signal=early_signal,
        ads_in_warning=warning,
        ads_in_stop=stop,
        ads_disabled=disabled,
        ads_claimed=claimed,
        ads_disabled_today=disabled_today,
        total_spend=total_spend,
        active_offers=active_offers,
        pending_disable_tasks=pending_tasks,
        pending_enable_tasks=pending_enable_tasks,
        enable_recommendations_ok=enable_recommendations_ok,
        enable_recommendations_early_signal=enable_recommendations_early_signal,
        enable_recommendations_warning=enable_recommendations_warning,
        last_scan_at=last_scan.isoformat() if last_scan else None,
        **_serialize_observer_runtime_fields(observer_row),
    )

    # 2. Получить ads (переиспользуем логику из list_ad_snapshots)
    q = select(AdSnapshot).order_by(AdSnapshot.last_observed_at.desc()).limit(limit)
    result = await db.execute(q)
    snapshots = result.scalars().all()
    diagnostics_map = await _build_snapshot_diagnostics_map(db, snapshots)

    ads = [
        AdSnapshotSchema(
            id=str(s.id),
            fb_ad_id=s.fb_ad_id,
            campaign_name=s.campaign_name,
            adset_name=s.adset_name,
            ad_name=s.ad_name,
            delivery_status=s.delivery_status,
            offer_code=s.resolved_offer_code,
            spend=s.spend,
            budget=getattr(s, "budget", "") or "",
            reach=int(getattr(s, "reach", 0) or 0),
            impressions=int(getattr(s, "impressions", 0) or 0),
            clicks=s.clicks,
            cpc=s.cpc,
            ctr=getattr(s, "ctr", None),
            cost_per_result=getattr(s, "cost_per_result", None),
            cpm=s.cpm,
            frequency=s.frequency,
            leads=s.leads,
            cost_per_lead=s.cost_per_lead,
            registrations=s.registrations,
            cost_per_registration=s.cost_per_registration,
            deposits=s.deposits,
            alert_state=s.alert_state.value,
            current_stage=s.current_stage.value if s.current_stage else None,
            early_signal_rule_codes=s.early_signal_rule_codes or [],
            warning_rule_codes=s.warning_rule_codes or [],
            stop_rule_codes=s.stop_rule_codes or [],
            cpm_diagnostic_status=diagnostics_map[s.fb_ad_id].cpm.status
            if s.fb_ad_id in diagnostics_map
            else None,
            frequency_diagnostic_status=(
                diagnostics_map[s.fb_ad_id].frequency.status
                if s.fb_ad_id in diagnostics_map
                else None
            ),
            diagnostic_short_text=(
                diagnostics_map[s.fb_ad_id].summary_text if s.fb_ad_id in diagnostics_map else None
            ),
            last_observed_at=(s.last_observed_at.isoformat() if s.last_observed_at else None),
        )
        for s in snapshots
    ]

    # 3. Получить incidents (переиспользуем логику из list_active_incidents)
    snapshot_query = (
        select(AdSnapshot)
        .where(
            AdSnapshot.last_observed_at >= scan_cutoff,
            AdSnapshot.alert_state.in_(
                [
                    AlertState.EARLY_SIGNAL_SENT,
                    AlertState.WARNING_SENT,
                    AlertState.STOP_SENT,
                    AlertState.CLAIMED,
                ]
            ),
        )
        .order_by(AdSnapshot.last_observed_at.desc())
    )

    incident_snapshots = (await db.execute(snapshot_query)).scalars().all()
    incidents: list[ActiveIncidentSchema] = []

    if incident_snapshots:
        incident_fb_ad_ids = [snapshot.fb_ad_id for snapshot in incident_snapshots]
        alert_events = (
            (
                await db.execute(
                    select(AlertEvent)
                    .where(AlertEvent.fb_ad_id.in_(incident_fb_ad_ids))
                    .order_by(AlertEvent.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        disable_tasks_for_incidents = (
            (
                await db.execute(
                    select(DisableTask)
                    .where(DisableTask.fb_ad_id.in_(incident_fb_ad_ids))
                    .order_by(DisableTask.updated_at.desc(), DisableTask.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

        events_by_ad: dict[str, list[AlertEvent]] = {}
        for event in alert_events:
            events_by_ad.setdefault(event.fb_ad_id, []).append(event)

        tasks_by_ad: dict[str, list[DisableTask]] = {}
        for task in disable_tasks_for_incidents:
            tasks_by_ad.setdefault(task.fb_ad_id, []).append(task)

        incidents = [
            _build_active_incident_schema(
                snapshot,
                alert_events=events_by_ad.get(snapshot.fb_ad_id, []),
                disable_tasks=tasks_by_ad.get(snapshot.fb_ad_id, []),
            )
            for snapshot in incident_snapshots
        ]
        incidents.sort(key=lambda incident: incident.last_activity_at, reverse=True)

    # 4. Получить disable-tasks (переиспользуем логику из list_disable_tasks)
    q_tasks = select(DisableTask).order_by(
        DisableTask.updated_at.desc(), DisableTask.created_at.desc()
    )
    q_tasks = q_tasks.where(
        DisableTask.status.in_(
            [
                DisableTaskStatus.PENDING,
                DisableTaskStatus.RUNNING,
                DisableTaskStatus.RETRYING,
                DisableTaskStatus.FAILED,
            ]
        )
    )
    q_tasks = q_tasks.limit(50)

    result_tasks = await db.execute(q_tasks)
    tasks = result_tasks.scalars().all()
    disable_tasks = [_serialize_disable_task(t) for t in tasks]

    return DashboardBatchSchema(
        ads=ads,
        stats=stats,
        incidents=incidents[:50],
        disable_tasks=disable_tasks,
    )


@router.get("/dashboard/ads", response_model=list[AdSnapshotSchema])
async def list_ad_snapshots(
    alert_state: str | None = Query(None),
    offer_code: str | None = Query(None),
    since_hours: int | None = Query(None, ge=1, le=168),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Список снимков объявлений (для таблицы в UI).

    since_hours — фильтр по last_observed_at: только объявления, виденные за
    последние N часов (None = все).
    """
    q = select(AdSnapshot).order_by(AdSnapshot.last_observed_at.desc())
    if alert_state:
        q = q.where(AdSnapshot.alert_state == AlertState(alert_state))
    if offer_code:
        q = q.where(
            func.lower(AdSnapshot.resolved_offer_code) == _offer_code_lookup_key(offer_code)
        )
    if since_hours is not None:
        cutoff = datetime.now(UTC) - timedelta(hours=since_hours)
        q = q.where(AdSnapshot.last_observed_at >= cutoff)
    q = q.limit(limit).offset(offset)

    result = await db.execute(q)
    snapshots = result.scalars().all()
    diagnostics_map = await _build_snapshot_diagnostics_map(db, snapshots)
    return [
        AdSnapshotSchema(
            id=str(s.id),
            fb_ad_id=s.fb_ad_id,
            campaign_name=s.campaign_name,
            adset_name=s.adset_name,
            ad_name=s.ad_name,
            delivery_status=s.delivery_status,
            offer_code=s.resolved_offer_code,
            spend=s.spend,
            budget=getattr(s, "budget", "") or "",
            reach=int(getattr(s, "reach", 0) or 0),
            impressions=int(getattr(s, "impressions", 0) or 0),
            clicks=s.clicks,
            cpc=s.cpc,
            ctr=getattr(s, "ctr", None),
            cost_per_result=getattr(s, "cost_per_result", None),
            cpm=s.cpm,
            frequency=s.frequency,
            leads=s.leads,
            cost_per_lead=s.cost_per_lead,
            registrations=s.registrations,
            cost_per_registration=s.cost_per_registration,
            deposits=s.deposits,
            alert_state=s.alert_state.value,
            current_stage=s.current_stage.value if s.current_stage else None,
            early_signal_rule_codes=s.early_signal_rule_codes or [],
            warning_rule_codes=s.warning_rule_codes or [],
            stop_rule_codes=s.stop_rule_codes or [],
            cpm_diagnostic_status=diagnostics_map[s.fb_ad_id].cpm.status
            if s.fb_ad_id in diagnostics_map
            else None,
            frequency_diagnostic_status=(
                diagnostics_map[s.fb_ad_id].frequency.status
                if s.fb_ad_id in diagnostics_map
                else None
            ),
            diagnostic_short_text=(
                diagnostics_map[s.fb_ad_id].summary_text if s.fb_ad_id in diagnostics_map else None
            ),
            last_observed_at=(s.last_observed_at.isoformat() if s.last_observed_at else None),
        )
        for s in snapshots
    ]


@router.get("/dashboard/incidents", response_model=list[ActiveIncidentSchema])
async def list_active_incidents(
    fb_ad_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Текущие открытые инциденты из актуальной скан-сессии."""
    last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
    scan_cutoff = _current_scan_cutoff(last_scan)

    snapshot_query = (
        select(AdSnapshot)
        .where(
            AdSnapshot.last_observed_at >= scan_cutoff,
            AdSnapshot.alert_state.in_(
                [
                    AlertState.EARLY_SIGNAL_SENT,
                    AlertState.WARNING_SENT,
                    AlertState.STOP_SENT,
                    AlertState.CLAIMED,
                ]
            ),
        )
        .order_by(AdSnapshot.last_observed_at.desc())
    )
    if fb_ad_id:
        snapshot_query = snapshot_query.where(AdSnapshot.fb_ad_id == fb_ad_id)

    snapshots = (await db.execute(snapshot_query)).scalars().all()
    if not snapshots:
        return []

    fb_ad_ids = [snapshot.fb_ad_id for snapshot in snapshots]
    alert_events = (
        (
            await db.execute(
                select(AlertEvent)
                .where(AlertEvent.fb_ad_id.in_(fb_ad_ids))
                .order_by(AlertEvent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    disable_tasks = (
        (
            await db.execute(
                select(DisableTask)
                .where(DisableTask.fb_ad_id.in_(fb_ad_ids))
                .order_by(DisableTask.updated_at.desc(), DisableTask.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    events_by_ad: dict[str, list[AlertEvent]] = {}
    for event in alert_events:
        events_by_ad.setdefault(event.fb_ad_id, []).append(event)

    tasks_by_ad: dict[str, list[DisableTask]] = {}
    for task in disable_tasks:
        tasks_by_ad.setdefault(task.fb_ad_id, []).append(task)

    incidents = [
        _build_active_incident_schema(
            snapshot,
            alert_events=events_by_ad.get(snapshot.fb_ad_id, []),
            disable_tasks=tasks_by_ad.get(snapshot.fb_ad_id, []),
        )
        for snapshot in snapshots
    ]
    incidents.sort(key=lambda incident: incident.last_activity_at, reverse=True)
    return incidents[:limit]


@router.get("/dashboard/alerts", response_model=list[AlertEventSchema])
async def list_alert_events(
    fb_ad_id: str | None = Query(None),
    stage: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """История алертов (для таблицы и модальных окон)."""
    q = select(AlertEvent).order_by(AlertEvent.created_at.desc())
    if fb_ad_id:
        q = q.where(AlertEvent.fb_ad_id == fb_ad_id)
    if stage:
        from core.domain import AlertStage as AS

        q = q.where(AlertEvent.stage == AS(stage))
    q = q.limit(limit).offset(offset)

    result = await db.execute(q)
    events = result.scalars().all()
    return [
        AlertEventSchema(
            id=str(e.id),
            incident_key=e.telegram_group_key,
            fb_ad_id=e.fb_ad_id,
            ad_name=e.ad_name,
            stage=e.stage.value,
            state=e.state.value,
            matched_rule_codes=e.matched_rule_codes or [],
            reason_title=e.reason_title,
            reason_text=e.reason_text,
            metrics_json=e.metrics_json or {},
            created_at=e.created_at.isoformat(),
        )
        for e in events
    ]


def _is_disable_task_stale_for_manual_restart(task: DisableTask, *, now: datetime) -> bool:
    """Проверяет, что RUNNING-задача действительно зависла."""
    last_activity_at = task.updated_at or task.created_at
    return last_activity_at <= now - DISABLE_TASK_STALE_TIMEOUT


@router.post("/dashboard/disable-tasks/{task_id}/retry")
async def retry_disable_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """Принудительно возвращает задачу отключения в очередь."""
    result = await db.execute(select(DisableTask).where(DisableTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    now = datetime.now(UTC)
    if task.status == DisableTaskStatus.RUNNING:
        if not _is_disable_task_stale_for_manual_restart(task, now=now):
            raise HTTPException(
                status_code=400, detail="Задача ещё выполняется и не считается зависшей"
            )
    elif task.status not in (DisableTaskStatus.RETRYING, DisableTaskStatus.FAILED):
        raise HTTPException(
            status_code=400, detail="Задача не в состоянии retry/failed/stale-running"
        )

    task.status = DisableTaskStatus.PENDING
    task.next_retry_at = None
    task.last_error = None
    task.completed_at = None
    await db.commit()
    return {"ok": True}


@router.post("/dashboard/disable-tasks", response_model=DisableTaskSchema, status_code=201)
async def create_disable_task(
    body: CreateDisableTaskRequest,
    db: AsyncSession = Depends(get_db),
) -> DisableTaskSchema:
    """Создаёт задачу на отключение объявления с идемпотентностью.

    Ищет AdSnapshot по fb_ad_id, затем создаёт DisableTask с использованием
    open_state_token как incident_key. Если задача с тем же fb_ad_id,
    incident_key и статусом PENDING/RUNNING/RETRYING уже существует,
    возвращает существующую задачу со статусом 200.
    """
    snapshot = await db.scalar(select(AdSnapshot).where(AdSnapshot.fb_ad_id == body.fb_ad_id))
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Снэпшот объявления не найден")

    incident_key = snapshot.open_state_token
    if incident_key is None:
        incident_key = str(_uuid.uuid4())
        snapshot.open_state_token = incident_key
        await db.flush()

    existing_task = await db.scalar(
        select(DisableTask).where(
            and_(
                DisableTask.fb_ad_id == body.fb_ad_id,
                DisableTask.open_state_token == incident_key,
                DisableTask.status.in_(
                    [
                        DisableTaskStatus.PENDING,
                        DisableTaskStatus.RUNNING,
                        DisableTaskStatus.RETRYING,
                    ]
                ),
            )
        )
    )
    if existing_task is not None:
        await db.rollback()
        return _serialize_disable_task(existing_task)

    new_task = DisableTask(
        fb_ad_id=body.fb_ad_id,
        ad_name=snapshot.ad_name,
        snapshot_id=snapshot.id,
        offer_id=snapshot.offer_id,
        open_state_token=incident_key,
        idempotency_key=f"dashboard_{body.fb_ad_id}_{incident_key}",
        status=DisableTaskStatus.PENDING,
        requested_by_username="dashboard",
    )
    db.add(new_task)
    await db.commit()
    await db.refresh(new_task)
    return _serialize_disable_task(new_task)


@router.get("/dashboard/disable-tasks", response_model=list[DisableTaskSchema])
async def list_disable_tasks(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Задачи на отключение (для мониторинга)."""
    q = select(DisableTask).order_by(DisableTask.updated_at.desc(), DisableTask.created_at.desc())
    if status:
        q = q.where(DisableTask.status == DisableTaskStatus(status))
    else:
        q = q.where(
            DisableTask.status.in_(
                [
                    DisableTaskStatus.PENDING,
                    DisableTaskStatus.RUNNING,
                    DisableTaskStatus.RETRYING,
                    DisableTaskStatus.FAILED,
                ]
            )
        )
    q = q.limit(limit).offset(offset)

    result = await db.execute(q)
    tasks = result.scalars().all()
    return [_serialize_disable_task(t) for t in tasks]


@router.get(
    "/api/dashboard/enable-recommendations",
    response_model=list[EnableRecommendationEventSchema],
)
async def list_enable_recommendations(
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Рекомендации на включение из текущего живого батча."""
    current_batch_marker, rows = await _load_current_enable_recommendations(db, limit=limit)
    if not rows:
        return []

    event_ids = [row.event.id for row in rows]
    tasks_result = await db.execute(
        select(EnableTask)
        .where(EnableTask.recommendation_event_id.in_(event_ids))
        .order_by(EnableTask.created_at.desc())
    )
    task_by_event_id: dict[_uuid.UUID, EnableTask] = {}
    for task in tasks_result.scalars().all():
        if task.recommendation_event_id and task.recommendation_event_id not in task_by_event_id:
            task_by_event_id[task.recommendation_event_id] = task

    return [
        _serialize_enable_recommendation_event(
            row.event,
            current_batch_marker=current_batch_marker,
            related_task=task_by_event_id.get(row.event.id),
            current_snapshot=row.snapshot,
            live_candidate=row.candidate,
        )
        for row in rows
    ]


@router.post("/dashboard/enable-recommendations/{event_id}/enable")
async def create_enable_task_from_recommendation(
    event_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Создаёт или переиспользует задачу на включение по recommendation event."""
    result = await promote_recommendation_to_enable_task(
        db,
        event_id=event_id,
        requested_by_username="dashboard",
    )
    if result.outcome in {"recommendation_not_found", "snapshot_not_found"}:
        raise HTTPException(status_code=404, detail=result.detail)
    if result.outcome not in {"created", "existing", "requeued"}:
        raise HTTPException(status_code=409, detail=result.detail)

    await db.commit()
    task = None
    if result.task_id:
        task = await db.scalar(
            select(EnableTask).where(EnableTask.id == _uuid.UUID(result.task_id))
        )

    return {
        "ok": True,
        "created_new": result.created_new,
        "detail": result.detail,
        "task": _serialize_enable_task(task).model_dump() if task else None,
    }


@router.get("/dashboard/enable-tasks", response_model=list[EnableTaskSchema])
async def list_enable_tasks(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Задачи на включение для мониторинга.

    По умолчанию показываем только актуальную последнюю задачу на каждое объявление,
    чтобы старые ошибки не маскировались под текущее состояние после успешного повтора.
    """
    if status:
        q = (
            select(EnableTask)
            .where(EnableTask.status == EnableTaskStatus(status))
            .order_by(EnableTask.updated_at.desc(), EnableTask.created_at.desc())
        )
    else:
        q = _build_current_enable_tasks_query(created_since=await _get_cabinet_day_start(db))
    q = q.limit(limit).offset(offset)

    result = await db.execute(q)
    tasks = result.scalars().all()
    return [_serialize_enable_task(task) for task in tasks]


@router.get("/dashboard/spend-history", response_model=list[SpendHistoryPoint])
async def get_spend_history(
    offer_code: str | None = Query(None),
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
):
    """История расходов — агрегация из AlertEvent по временным бакетам."""
    # Возвращаем последние снэпшоты как историю
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    q = (
        select(AdSnapshot)
        .where(AdSnapshot.last_observed_at >= cutoff)
        .order_by(AdSnapshot.last_observed_at.asc())
    )
    if offer_code:
        q = q.where(
            func.lower(AdSnapshot.resolved_offer_code) == _offer_code_lookup_key(offer_code)
        )

    result = await db.execute(q)
    snapshots = result.scalars().all()
    return [
        SpendHistoryPoint(
            timestamp=s.last_observed_at.isoformat(),
            spend=s.spend,
            clicks=s.clicks,
            leads=s.leads,
            registrations=s.registrations,
            deposits=s.deposits,
        )
        for s in snapshots
    ]


@router.get("/dashboard/chart-data", response_model=ChartDataSchema)
async def get_chart_data(
    period: str = Query("today", pattern="^(today|7d|30d)$"),
    db: AsyncSession = Depends(get_db),
):
    """Данные для операционной аналитики dashboard с учётом выбранного периода."""
    now = _dashboard_now()
    snapshot_cutoff = await _resolve_dashboard_snapshot_cutoff(db)
    history_cutoff = snapshot_cutoff if period == "today" else _performance_cutoff(period, now)
    event_cutoff = await _resolve_dashboard_event_cutoff(db, period=period, now=now)

    # 1. Алерты по выбранному периоду
    alerts_result = await db.execute(
        select(AlertEvent.stage, AlertEvent.created_at)
        .where(AlertEvent.created_at >= event_cutoff)
        .order_by(AlertEvent.created_at.asc())
    )
    alert_rows = alerts_result.all()

    alerts_timeline: dict[datetime, dict] = {}
    bucket_cursor = _timeline_bucket_start(event_cutoff, period)
    last_bucket = _timeline_bucket_start(now, period)
    while bucket_cursor <= last_bucket:
        label = _timeline_bucket_label(bucket_cursor, period)
        alerts_timeline[bucket_cursor] = {
            "hour": label,
            "label": label,
            "early_signal": 0,
            "warning": 0,
            "stop": 0,
        }
        bucket_cursor += _timeline_bucket_step(period)
    for stage, created_at in alert_rows:
        bucket = _timeline_bucket_start(_to_dashboard_timezone(created_at), period)
        if bucket in alerts_timeline:
            if stage == AlertStage.EARLY_SIGNAL:
                alerts_timeline[bucket]["early_signal"] += 1
            elif stage == AlertStage.WARNING:
                alerts_timeline[bucket]["warning"] += 1
            elif stage == AlertStage.STOP:
                alerts_timeline[bucket]["stop"] += 1
    alerts_by_hour = [row for _, row in sorted(alerts_timeline.items(), key=lambda item: item[0])]

    # 2. Кампании за период собираем тем же способом, что верхний performance-блок.
    snapshot_result = await db.execute(
        select(AdSnapshot)
        .where(AdSnapshot.last_observed_at >= snapshot_cutoff)
        .order_by(AdSnapshot.last_observed_at.asc())
    )
    snapshots = snapshot_result.scalars().all()
    rule_violations = _build_current_risk_reason_rows(snapshots)
    archives = []
    if period != "today":
        archives = await _load_dashboard_archives(db, cutoff=history_cutoff)
    performance_payload = _build_dashboard_performance_payload(
        snapshots,
        period=period,
        now=now,
        cutoff=history_cutoff,
        archives=archives,
    )
    offer_rule_map = await _load_offer_rules_for_snapshots(db, snapshots)
    campaigns = [
        {
            "campaign": row.campaign[:30] + "…" if len(row.campaign) > 30 else row.campaign,
            "campaign_full": row.campaign,
            "spend": float(row.spend or 0),
            "deposits": int(row.deposits or 0),
            "leads": int(row.leads or 0),
            "registrations": int(row.registrations or 0),
        }
        for row in performance_payload.campaigns[:10]
    ]
    campaign_budget_deltas = _build_campaign_stop_overrun_rows(snapshots, offer_rule_map)

    # 3. Распределение статусов — только по актуальному живому срезу.
    state_result = await db.execute(
        select(AdSnapshot.alert_state, func.count().label("cnt"))
        .where(AdSnapshot.last_observed_at >= snapshot_cutoff)
        .group_by(AdSnapshot.alert_state)
    )
    _state_labels = {
        AlertState.NORMAL: "Норма",
        AlertState.EARLY_SIGNAL_SENT: "Ранний сигнал",
        AlertState.WARNING_SENT: "Предупреждение",
        AlertState.STOP_SENT: "Стоп",
        AlertState.CLAIMED: "Ожидает OFF",
        AlertState.DISABLED: "Отключён",
    }
    state_distribution = [
        {"state": _state_labels.get(state, str(state)), "count": cnt}
        for state, cnt in state_result.all()
    ]

    # 4. Топ объявлений по расходу — текущий живой срез, без исторического режима.
    top_ads_result = await db.execute(
        select(
            AdSnapshot.ad_name,
            AdSnapshot.adset_name,
            AdSnapshot.fb_ad_id,
            AdSnapshot.spend,
            AdSnapshot.clicks,
            AdSnapshot.leads,
            AdSnapshot.deposits,
            AdSnapshot.alert_state,
        )
        .where(AdSnapshot.last_observed_at >= snapshot_cutoff)
        .where(AdSnapshot.spend > 0)
        .order_by(AdSnapshot.spend.desc())
        .limit(8)
    )
    _state_icons = {
        AlertState.EARLY_SIGNAL_SENT: "🔎",
        AlertState.STOP_SENT: "🛑",
        AlertState.WARNING_SENT: "⚠️",
        AlertState.CLAIMED: "🔄",
        AlertState.DISABLED: "🚫",
        AlertState.NORMAL: "✅",
    }
    top_ads_by_spend = [
        {
            "name": row.ad_name[:25] + "…" if len(row.ad_name) > 25 else row.ad_name,
            "name_full": row.ad_name,
            "adset_name": row.adset_name,
            "adset_short": row.adset_name[:18] + "…"
            if len(row.adset_name) > 18
            else row.adset_name,
            "label": (
                f"{row.ad_name[:16] + '…' if len(row.ad_name) > 16 else row.ad_name} · "
                f"{row.adset_name[:10] + '…' if len(row.adset_name) > 10 else row.adset_name}"
            ),
            "fb_ad_id": row.fb_ad_id,
            "spend": float(row.spend or 0),
            "clicks": int(row.clicks or 0),
            "leads": int(row.leads or 0),
            "deposits": int(row.deposits or 0),
            "state": row.alert_state.value if row.alert_state else "NORMAL",
            "state_icon": _state_icons.get(row.alert_state, "✅"),
        }
        for row in top_ads_result.all()
    ]

    return ChartDataSchema(
        alerts_by_hour=alerts_by_hour,
        rule_violations=rule_violations,
        campaigns=campaigns,
        state_distribution=state_distribution,
        top_ads_by_spend=top_ads_by_spend,
        campaign_budget_deltas=campaign_budget_deltas,
        campaign_stop_overruns=campaign_budget_deltas,
    )


# ==========================================
# Эндпоинт — Таймлайн объявления
# ==========================================


@router.get("/ads/{fb_ad_id}/timeline")
async def get_ad_timeline(fb_ad_id: str, db: AsyncSession = Depends(get_db)):
    """Таймлайн событий по одному объявлению: алерты, метрики на каждый момент, динамика расхода."""
    # Текущий снэпшот
    snapshot_result = await db.execute(select(AdSnapshot).where(AdSnapshot.fb_ad_id == fb_ad_id))
    snapshot = snapshot_result.scalar_one_or_none()

    # История алертов
    events_result = await db.execute(
        select(AlertEvent)
        .where(AlertEvent.fb_ad_id == fb_ad_id)
        .order_by(AlertEvent.created_at.desc())
    )
    events = events_result.scalars().all()

    # Задачи на отключение
    tasks_result = await db.execute(
        select(DisableTask)
        .where(DisableTask.fb_ad_id == fb_ad_id)
        .order_by(DisableTask.created_at.desc())
    )
    tasks = tasks_result.scalars().all()

    recommendation_events_result = await db.execute(
        select(EnableRecommendationEvent)
        .where(EnableRecommendationEvent.fb_ad_id == fb_ad_id)
        .order_by(EnableRecommendationEvent.created_at.desc())
    )
    recommendation_events = recommendation_events_result.scalars().all()

    enable_tasks_result = await db.execute(
        select(EnableTask)
        .where(EnableTask.fb_ad_id == fb_ad_id)
        .order_by(EnableTask.created_at.desc())
    )
    enable_tasks = enable_tasks_result.scalars().all()

    diagnostics = None
    if snapshot is not None:
        diagnostics_map = await _build_snapshot_diagnostics_map(db, [snapshot])
        diagnostics = diagnostics_map.get(snapshot.fb_ad_id)
    current_incident_key = _incident_key_for_snapshot(snapshot) if snapshot is not None else None
    current_incident = (
        _build_active_incident_schema(
            snapshot,
            alert_events=events,
            disable_tasks=tasks,
        )
        if snapshot is not None
        and snapshot.alert_state
        in (
            AlertState.EARLY_SIGNAL_SENT,
            AlertState.WARNING_SENT,
            AlertState.STOP_SENT,
            AlertState.CLAIMED,
        )
        else None
    )

    # Формируем таймлайн: объединяем алерты и задачи по времени
    timeline = []
    for e in events:
        m = e.metrics_json or {}
        timeline.append(
            {
                "type": "alert",
                "time": e.created_at.isoformat(),
                "stage": e.stage.value if e.stage else None,
                "state": e.state.value if e.state else None,
                "incident_key": e.telegram_group_key,
                "current_incident": bool(
                    current_incident_key and e.telegram_group_key == current_incident_key
                ),
                "matched_rules": e.matched_rule_codes or [],
                "reason_title": e.reason_title,
                "reason_text": e.reason_text,
                "spend": m.get("spend"),
                "budget": m.get("budget"),
                "reach": m.get("reach"),
                "impressions": m.get("impressions"),
                "clicks": m.get("clicks"),
                "cpc": m.get("cpc"),
                "ctr": m.get("ctr"),
                "cost_per_result": m.get("cost_per_result"),
                "cpm": m.get("cpm"),
                "frequency": m.get("frequency"),
                "leads": m.get("leads"),
                "registrations": m.get("registrations"),
                "cost_per_registration": m.get("cost_per_registration"),
                "deposits": m.get("deposits"),
            }
        )
    for t in tasks:
        timeline.append(
            {
                "type": "disable_task",
                "time": t.created_at.isoformat(),
                "incident_key": t.open_state_token,
                "current_incident": bool(
                    current_incident_key and t.open_state_token == current_incident_key
                ),
                "status": t.status.value,
                "attempt_count": t.attempt_count,
                "requested_by": t.requested_by_username,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "last_error": t.last_error,
            }
        )
    for event in recommendation_events:
        reason_title, reason_text = _normalize_enable_recommendation_reason(
            recommendation_level=event.recommendation_level,
            reason_title=event.reason_title,
            reason_text=event.reason_text,
        )
        timeline.append(
            {
                "type": "enable_recommendation",
                "time": event.created_at.isoformat(),
                "recommendation_level": event.recommendation_level.value,
                "delivery_status": event.delivery_status,
                "matched_rule_codes": event.matched_rule_codes or [],
                "reason_title": reason_title,
                "reason_text": reason_text,
                "metrics_json": event.metrics_json or {},
            }
        )
    for task in enable_tasks:
        timeline.append(
            {
                "type": "enable_task",
                "time": task.created_at.isoformat(),
                "status": task.status.value,
                "attempt_count": task.attempt_count,
                "requested_by": task.requested_by_username,
                "recommendation_event_id": (
                    str(task.recommendation_event_id) if task.recommendation_event_id else None
                ),
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "last_error": task.last_error,
            }
        )

    # Показываем новые события сверху, чтобы таймлайн читался как журнал.
    timeline.sort(key=lambda x: x["time"], reverse=True)

    return {
        "fb_ad_id": fb_ad_id,
        "ad_name": snapshot.ad_name if snapshot else None,
        "campaign_name": snapshot.campaign_name if snapshot else None,
        "adset_name": snapshot.adset_name if snapshot else None,
        "current_state": snapshot.alert_state.value if snapshot else None,
        "delivery_status": snapshot.delivery_status if snapshot else None,
        "current_incident": current_incident.model_dump() if current_incident else None,
        "current_metrics": {
            "spend": str(snapshot.spend) if snapshot else None,
            "budget": getattr(snapshot, "budget", None) if snapshot else None,
            "reach": getattr(snapshot, "reach", None) if snapshot else None,
            "impressions": getattr(snapshot, "impressions", None) if snapshot else None,
            "clicks": snapshot.clicks if snapshot else None,
            "cpc": str(snapshot.cpc) if snapshot and snapshot.cpc is not None else None,
            "ctr": (
                str(snapshot.ctr)
                if snapshot and getattr(snapshot, "ctr", None) is not None
                else None
            ),
            "delivery_status": snapshot.delivery_status if snapshot else None,
            "cost_per_result": (
                str(snapshot.cost_per_result)
                if snapshot and getattr(snapshot, "cost_per_result", None) is not None
                else None
            ),
            "cpm": str(snapshot.cpm) if snapshot and snapshot.cpm is not None else None,
            "frequency": str(snapshot.frequency)
            if snapshot and snapshot.frequency is not None
            else None,
            "leads": snapshot.leads if snapshot else None,
            "cost_per_lead": str(snapshot.cost_per_lead)
            if snapshot and snapshot.cost_per_lead is not None
            else None,
            "registrations": snapshot.registrations if snapshot else None,
            "cost_per_registration": (
                str(snapshot.cost_per_registration)
                if snapshot and snapshot.cost_per_registration is not None
                else None
            ),
            "deposits": snapshot.deposits if snapshot else None,
        }
        if snapshot
        else None,
        "diagnostics": diagnostics.model_dump() if diagnostics else None,
        "last_observed_at": snapshot.last_observed_at.isoformat() if snapshot else None,
        "timeline": timeline,
    }


# ==========================================
# Эндпоинты — Vision настройки
# ==========================================
