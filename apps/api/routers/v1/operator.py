"""Versioned operator snapshot, ads and command lifecycle endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Header, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from apps.api.deps import DepEngine, DepSettings
from apps.api.routers.v1.schemas.operator import (
    ApiProblem,
    DataState,
    OperatorActionItem,
    OperatorActionManualReview,
    OperatorActionsData,
    OperatorActionsResponse,
    OperatorActionState,
    OperatorAdCommandRequest,
    OperatorAdRow,
    OperatorAdsResponse,
    OperatorApproachingStopData,
    OperatorAttentionAction,
    OperatorAttentionData,
    OperatorAttentionItem,
    OperatorAttentionTarget,
    OperatorCabinetLedgerRow,
    OperatorCommandResponse,
    OperatorCurrencyGroup,
    OperatorEconomyData,
    OperatorEconomyTotals,
    OperatorEventItem,
    OperatorFunnelData,
    OperatorFunnelStage,
    OperatorIncidentAckResponse,
    OperatorIncidentDetailResponse,
    OperatorIncidentItem,
    OperatorIncidentsResponse,
    OperatorIssue,
    OperatorManualReviewObservation,
    OperatorManualReviewRequest,
    OperatorManualReviewResponse,
    OperatorPortfolioData,
    OperatorScopeEvidence,
    OperatorSection,
    OperatorSeverity,
    OperatorSnapshot,
    OperatorSnapshotMeta,
    OperatorSpendPoint,
    OperatorSystemData,
    OperatorWorkerState,
)
from apps.api.utils.status_mapper import to_frontend_task_status
from core.analytics import DEFAULT_ANALYTICS_WINDOW
from core.analytics.performance import (
    aggregate_performance,
    fetch_live_budget_points,
    fetch_performance_rows,
    fetch_source_quality,
)
from core.commands.service import (
    CommandConflictError,
    CommandNotFoundError,
    CommandPreconditionError,
    CommandService,
    principal_scoped_idempotency_key,
)
from core.dashboard import stats_queries as stats_queries
from core.incidents.service import (
    IncidentNotAcknowledgeableError,
    IncidentNotFoundError,
    acknowledge_incident,
)
from core.meta_api.account_tz import (
    AccountCurrencyResolution,
    CabinetDayResolution,
    cabinet_day_end_for_timezone,
    canonical_account_id,
    resolve_account_currencies,
    resolve_cabinet_days,
)
from core.observer.accounts import nothing_monitored_reason_for, resolve_configured_ad_account_ids
from core.observer.login_required import LOGIN_REQUIRED_INCIDENT_PREFIX
from core.operator.queries import (
    fetch_operator_actions,
    fetch_operator_ads,
    fetch_operator_events,
    fetch_operator_incident,
    fetch_operator_incident_page,
    fetch_operator_incidents,
    fetch_operator_revision,
    fetch_operator_scan_state,
    fetch_worker_heartbeats,
)
from core.public_identifiers import parse_public_uuid, public_uuid
from core.safe_diagnostics import redact_sensitive_text
from core.scanner.status import DELIVERY_DISABLED_STATUSES, normalized_delivery_status
from core.tasks.queue import (
    ManualReviewNotApplicableError,
    ManualReviewTaskNotFoundError,
    record_manual_reconciliation,
)
from core.worker_liveness import (
    WORKER_POLL_INTERVAL_SECONDS,
    heartbeat_stale_after_seconds,
    poll_stale_after_seconds,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/operator", tags=["operator"])

_MONEY = Decimal("0.01")
_MONEY_RULE_CODES = {
    "cpc_stop",
    "cpl_stop",
    "cpr_stop",
    "spend_no_dep_range",
    "spend_with_dep_range",
}
_PROBLEM_RESPONSES = {
    401: {"model": ApiProblem, "description": "Authentication failed"},
    403: {"model": ApiProblem, "description": "Permission denied"},
    404: {"model": ApiProblem, "description": "Resource not found"},
    409: {"model": ApiProblem, "description": "Command lifecycle conflict"},
    412: {"model": ApiProblem, "description": "Ad projection changed before enqueue"},
    422: {"model": ApiProblem, "description": "Request validation failed"},
    503: {"model": ApiProblem, "description": "Operator source unavailable"},
}
_COMMAND_RESPONSES = {
    200: {
        "model": OperatorCommandResponse,
        "description": "Existing command lifecycle state",
    },
    **_PROBLEM_RESPONSES,
}
_EVENTS_MAX_RANGE_DAYS = 90

# Русские подписи одиннадцати фоновых воркеров (issue #176) — не путать с
# per-cabinet scan actors из cabinet_runtime, которые исторически подписаны
# «воркеры» на экране, хотя ими не являются.
_BACKGROUND_WORKER_LABELS: dict[str, str] = {
    "campaign_creator": "Создание кампаний",
    "autopause": "Авто-стоп (money)",
    "meta_api": "Meta API исполнитель",
    "cleanup": "Очистка",
    "digest_scheduler": "Дайджест",
    "health_watchdog": "Сторож здоровья",
    "observer": "Наблюдение (процесс)",
    "reconciler": "Сверка задач",
    "telegram_delivery": "Telegram · доставка",
    "telegram_updates": "Telegram · приём",
    "tracker_reconciliation_worker": "Сверка трекера",
}


def _problem(*, status_code: int, code: str, message: str, correlation_id: str) -> JSONResponse:
    problem = ApiProblem(
        code=code,
        message=message,
        correlation_id=correlation_id,
        field_errors=None,
    )
    return JSONResponse(status_code=status_code, content=problem.model_dump(mode="json"))


def _public_incident_id(value: object) -> str:
    """Непрозрачный публичный id инцидента вместо внутреннего UUID."""
    return public_uuid(value, prefix="inc")


def _public_request_id(value: object) -> str:
    """Непрозрачный публичный id команды/подтверждения вместо внутреннего UUID."""
    return public_uuid(value, prefix="req")


def _age(now: datetime, value: datetime | None) -> int | None:
    return max(0, int((now - value).total_seconds())) if value else None


def _scope_evidence(
    *,
    cabinet_days: CabinetDayResolution,
    currencies: AccountCurrencyResolution,
    display_timezone: str,
) -> OperatorScopeEvidence:
    return OperatorScopeEvidence(
        account_ids=list(cabinet_days.account_ids),
        display_timezone=display_timezone,
        cabinet_timezone=cabinet_days.cabinet_timezone,
        cabinet_timezone_state=cabinet_days.timezone_state,
        missing_timezone_account_ids=list(cabinet_days.missing_account_ids),
        currency=currencies.currency,
        currency_state=currencies.state,
        missing_currency_account_ids=list(currencies.missing_account_ids),
        currency_observed_at=currencies.observed_at,
    )


def _currency_issue(currencies: AccountCurrencyResolution) -> OperatorIssue | None:
    """Issue 354: `unknown` (снимок протух/не получен) — самая частая причина в

    живой системе, куда чаще настоящего не-долларового кабинета. Три причины
    различаются кодом и текстом действия; `single` (любая валюта, не только
    USD) ничего не скрывает — денежные суммы уже посчитаны в валюте кабинета,
    и `OperatorScopeEvidence.currency` уже несёт её код для подписи в UI.
    """
    if currencies.state == "single":
        return None
    if currencies.state == "mixed":
        return OperatorIssue(
            code="currency_mixed",
            title="В выборке несколько валют",
            detail=(
                "Единой валюты нет — сквозной итог не считается. "
                "Сузьте выборку до одного кабинета, чтобы увидеть суммы."
            ),
            severity=OperatorSeverity.UNKNOWN,
            correlation_id=None,
        )
    missing = ", ".join(f"act_{value}" for value in currencies.missing_account_ids)
    return OperatorIssue(
        code="currency_unknown",
        title="Валюта кабинета не подтверждена",
        detail=(
            f"Нет подтверждённой валюты для: {missing}. "
            "Обновите снимок кабинета, чтобы увидеть суммы."
            if missing
            else "Не найден кабинет с подтверждённой валютой. Обновите снимок кабинета."
        ),
        severity=OperatorSeverity.UNKNOWN,
        correlation_id=None,
    )


def _fail_closed_snapshot_money(
    *,
    economy: OperatorSection[OperatorEconomyData],
    funnel: OperatorSection[OperatorFunnelData],
    currencies: AccountCurrencyResolution,
) -> tuple[OperatorSection[OperatorEconomyData], OperatorSection[OperatorFunnelData]]:
    issue = _currency_issue(currencies)
    if issue is None:
        return economy, funnel
    economy_issues = [*economy.issues, issue]
    if economy.data is not None:
        economy_data = OperatorEconomyData(
            totals=OperatorEconomyTotals(
                spend=None,
                base=None,
                stop=None,
                base_delta=None,
            ),
            series=[
                OperatorSpendPoint(at=point.at, actual=None, base=None, stop=None)
                for point in economy.data.series
            ],
        )
    else:
        economy_data = None
    funnel_data = (
        OperatorFunnelData(
            stages=[stage.model_copy(update={"cost": None}) for stage in funnel.data.stages]
        )
        if funnel.data is not None
        else None
    )
    return (
        economy.model_copy(
            update={
                "state": DataState.PARTIAL if economy_data is not None else DataState.UNAVAILABLE,
                "issues": economy_issues,
                "data": economy_data,
            }
        ),
        funnel.model_copy(
            update={
                "state": DataState.PARTIAL if funnel_data is not None else DataState.UNAVAILABLE,
                "issues": [*funnel.issues, issue],
                "data": funnel_data,
            }
        ),
    )


_STATE_RANK: dict[DataState, int] = {
    DataState.READY: 0,
    DataState.EMPTY: 0,
    DataState.PARTIAL: 1,
    DataState.STALE: 2,
    DataState.UNAVAILABLE: 3,
}
_SEVERITY_RANK: dict[OperatorSeverity, int] = {
    OperatorSeverity.OK: 0,
    OperatorSeverity.UNKNOWN: 1,
    OperatorSeverity.WARNING: 2,
    OperatorSeverity.CRITICAL: 3,
}


def _combined_data_state(states: list[DataState]) -> DataState:
    if not states or all(state == DataState.EMPTY for state in states):
        return DataState.EMPTY
    if all(state == DataState.UNAVAILABLE for state in states):
        return DataState.UNAVAILABLE
    if all(state == DataState.STALE for state in states):
        return DataState.STALE
    if any(_STATE_RANK[state] > 0 for state in states):
        return DataState.PARTIAL
    return DataState.READY


def _combined_severity(values: list[OperatorSeverity]) -> OperatorSeverity:
    return max(values, key=_SEVERITY_RANK.__getitem__, default=OperatorSeverity.UNKNOWN)


def _sum_complete_money(
    cabinets: list[OperatorCabinetLedgerRow],
    field: Literal["spend", "base", "stop"],
) -> str | None:
    values = [getattr(cabinet.totals, field) for cabinet in cabinets]
    if not values or any(value is None for value in values):
        return None
    return _money(sum((Decimal(value) for value in values if value is not None), Decimal(0)))


def _portfolio_totals(cabinets: list[OperatorCabinetLedgerRow]) -> OperatorEconomyTotals:
    spend = _sum_complete_money(cabinets, "spend")
    base = _sum_complete_money(cabinets, "base")
    stop = _sum_complete_money(cabinets, "stop")
    return OperatorEconomyTotals(
        spend=spend,
        base=base,
        stop=stop,
        base_delta=(
            _money(Decimal(spend) - Decimal(base))
            if spend is not None and base is not None
            else None
        ),
    )


def _unknown_money_totals() -> OperatorEconomyTotals:
    return OperatorEconomyTotals(spend=None, base=None, stop=None, base_delta=None)


def _usd_safe_portfolio_rows(
    rows: list[OperatorCabinetLedgerRow],
    currency: str | None,
) -> list[OperatorCabinetLedgerRow]:
    if currency == "USD":
        return rows
    issue = OperatorIssue(
        code="currency_not_usd" if currency else "currency_unknown",
        title="Кабинет настроен не в USD" if currency else "Валюта кабинета не подтверждена",
        detail="Денежные суммы скрыты: FB Agent работает только с долларовыми бюджетами.",
        severity=OperatorSeverity.UNKNOWN,
        correlation_id=None,
    )
    safe_rows: list[OperatorCabinetLedgerRow] = []
    for row in rows:
        issues = (
            row.issues
            if any(item.code == issue.code for item in row.issues)
            else [*row.issues, issue]
        )
        safe_rows.append(
            row.model_copy(
                update={
                    "state": (
                        DataState.PARTIAL
                        if row.state in {DataState.READY, DataState.EMPTY}
                        else row.state
                    ),
                    "severity": OperatorSeverity.UNKNOWN,
                    "totals": _unknown_money_totals(),
                    "risk_label": "Валюта не подтверждена",
                    "risk_reason": issue.detail,
                    "issues": issues,
                }
            )
        )
    return safe_rows


def _cabinet_risk(
    *,
    state: DataState,
    totals: OperatorEconomyTotals,
    issues: list[OperatorIssue],
    currency: str | None,
) -> tuple[OperatorSeverity, str, str | None]:
    if state == DataState.UNAVAILABLE:
        return OperatorSeverity.UNKNOWN, "Данные недоступны", issues[0].title if issues else None
    if state == DataState.STALE:
        return OperatorSeverity.UNKNOWN, "Снимок устарел", issues[0].title if issues else None

    spend = Decimal(totals.spend) if totals.spend is not None else None
    base = Decimal(totals.base) if totals.base is not None else None
    stop = Decimal(totals.stop) if totals.stop is not None else None

    def money_label(value: Decimal) -> str:
        return (
            f"${value}" if currency == "USD" else f"{value} {currency}" if currency else str(value)
        )

    if spend is not None and stop is not None and spend >= stop:
        return (
            OperatorSeverity.CRITICAL,
            "Stop-граница достигнута",
            f"Факт {money_label(spend)} ≥ stop {money_label(stop)}",
        )
    if state == DataState.PARTIAL:
        return (
            OperatorSeverity.WARNING,
            "Данные неполные",
            issues[0].title if issues else "Часть доказательств ещё не подтверждена.",
        )
    if spend is None:
        return OperatorSeverity.UNKNOWN, "Расход не подтверждён", None
    if base is None or stop is None:
        return OperatorSeverity.UNKNOWN, "Пороги не подтверждены", None
    if spend >= base:
        return (
            OperatorSeverity.WARNING,
            "Расход выше базы",
            f"Факт {money_label(spend)} ≥ база {money_label(base)}",
        )
    return (
        OperatorSeverity.OK,
        "В пределах порогов",
        f"До stop осталось {money_label(stop - spend)}",
    )


def _currency_groups(
    cabinets: list[OperatorCabinetLedgerRow],
) -> list[OperatorCurrencyGroup]:
    grouped: dict[str | None, list[OperatorCabinetLedgerRow]] = {}
    for cabinet in cabinets:
        grouped.setdefault(cabinet.currency, []).append(cabinet)

    groups: list[OperatorCurrencyGroup] = []
    for currency, rows in sorted(
        grouped.items(),
        key=lambda item: (item[0] is None, item[0] or ""),
    ):
        rows = _usd_safe_portfolio_rows(rows, currency)
        states = [row.state for row in rows]
        groups.append(
            OperatorCurrencyGroup(
                id=currency or "unknown",
                currency=currency,
                state=_combined_data_state(states),
                severity=_combined_severity([row.severity for row in rows]),
                as_of=min((row.as_of for row in rows if row.as_of is not None), default=None),
                freshness_seconds=max(
                    (row.freshness_seconds for row in rows if row.freshness_seconds is not None),
                    default=None,
                ),
                totals=_portfolio_totals(rows),
                cabinets=rows,
            )
        )
    return groups


def _money(value: Any) -> str | None:
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(_MONEY, rounding=ROUND_HALF_UP))


def _ratio(numerator: int | None, denominator: int | None) -> str | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return str(
        (Decimal(numerator) * Decimal("100") / Decimal(denominator)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )


def _cost(spend: str | None, count: int | None) -> str | None:
    if spend is None or count is None or count <= 0:
        return None
    return _money(Decimal(spend) / Decimal(count))


def _operator_events_window(
    period: Literal["today", "7d", "30d", "custom"],
    from_date: date | None,
    to_date: date | None,
) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), now
    if period in {"7d", "30d"}:
        return now - timedelta(days=7 if period == "7d" else 30), now
    if from_date is None or to_date is None:
        raise ValueError("Для своего периода нужны from_date и to_date")
    if to_date < from_date:
        raise ValueError("to_date должен быть >= from_date")
    if (to_date - from_date).days + 1 > _EVENTS_MAX_RANGE_DAYS:
        raise ValueError(f"Диапазон не может превышать {_EVENTS_MAX_RANGE_DAYS} дней")
    return (
        datetime.combine(from_date, time.min, tzinfo=UTC),
        datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=UTC),
    )


def _ads_section_state(
    *,
    meta_as_of: datetime | None,
    meta_freshness: int | None,
    meta_status: str | None,
    row_state: str,
    total: int,
    timezone_known: bool,
    tracker_available: bool,
) -> DataState:
    """Derive the collection state without hiding degraded rows or sources."""
    if meta_as_of is None or row_state == DataState.UNAVAILABLE:
        return DataState.UNAVAILABLE
    if row_state == DataState.PARTIAL:
        return DataState.PARTIAL
    if not timezone_known or not tracker_available:
        return DataState.PARTIAL
    if meta_status == "degraded" and (meta_freshness is None or meta_freshness <= 60):
        return DataState.PARTIAL
    if row_state == DataState.STALE or (meta_freshness is not None and meta_freshness > 60):
        return DataState.STALE
    if total == 0:
        return DataState.EMPTY
    return DataState.READY


async def _window(
    engine: Any,
    name: Literal["today", "24h", "7d", "30d"],
    *,
    account_id: str | None = None,
    now: datetime | None = None,
) -> tuple[
    datetime,
    datetime,
    bool,
    dict[str, datetime] | None,
    CabinetDayResolution,
]:
    observed_now = now or datetime.now(UTC)
    cabinet_days = await resolve_cabinet_days(
        engine,
        account_ids=[account_id] if account_id else None,
        now=observed_now,
    )
    if name == "today":
        fallback = observed_now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = min(cabinet_days.query_boundaries.values(), default=fallback)
        return start, observed_now, True, cabinet_days.query_boundaries, cabinet_days
    duration = (
        timedelta(hours=24)
        if name == "24h"
        else timedelta(days=30)
        if name == "30d"
        else DEFAULT_ANALYTICS_WINDOW
    )
    start = observed_now - duration
    return start, observed_now, False, None, cabinet_days


async def _account_meta(engine: Any, account_id: str | None) -> dict[str, str | None]:
    canonical_account_id = account_id.removeprefix("act_") if account_id else None
    account_filter = (
        "ad_account_id IS NOT NULL"
        if canonical_account_id is None
        else "ad_account_id = :account_id"
    )
    params = {} if canonical_account_id is None else {"account_id": canonical_account_id}
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    f"""
                    SELECT ad_account_id, COUNT(*) AS campaign_count
                    FROM fb_campaigns
                    WHERE {account_filter}
                    GROUP BY ad_account_id
                    ORDER BY campaign_count DESC, ad_account_id
                    LIMIT 1
                    """
                ),
                params,
            )
        ).first()
    if row is None:
        return {"id": canonical_account_id, "name": None}
    resolved = str(row.ad_account_id)
    return {"id": resolved, "name": f"act_{resolved}"}


async def _analytics_sections(
    *,
    engine: Any,
    account_id: str | None,
    window_name: Literal["today", "24h", "7d", "30d"],
    now: datetime,
) -> tuple[
    OperatorSection[OperatorEconomyData],
    OperatorSection[OperatorFunnelData],
    bool,
    datetime,
    datetime,
    CabinetDayResolution,
]:
    from_dt, to_dt, is_live, boundaries, cabinet_days = await _window(
        engine,
        window_name,
        account_id=account_id,
        now=now,
    )
    sources = await fetch_source_quality(
        engine,
        from_dt=from_dt,
        to_dt=to_dt,
        cabinet_days=cabinet_days,
        account_id=account_id,
    )
    rows_task = fetch_performance_rows(
        engine,
        from_dt=from_dt,
        to_dt=to_dt,
        is_live=is_live,
        level="campaign",
        parent_id=None,
        account_id=account_id,
        offer_id=None,
        campaign_id=None,
        search=None,
        cabinet_boundaries=boundaries,
        tracker_available=sources["tracker"]["status"] == "good",
    )
    if is_live:
        series_task = fetch_live_budget_points(
            engine,
            from_dt=from_dt,
            to_dt=to_dt,
            account_id=account_id,
            offer_id=None,
            campaign_id=None,
            cabinet_boundaries=boundaries,
        )
    else:
        series_task = stats_queries.fetch_daily_series(
            engine,
            from_dt=from_dt,
            to_dt=to_dt,
            account_id=account_id,
        )
    raw_rows, raw_series = await asyncio.gather(rows_task, series_task)
    aggregate = aggregate_performance(
        raw_rows,
        level="campaign",
        is_live=is_live,
        sort="spend",
        direction="desc",
        page=1,
        page_size=50,
    )
    totals = aggregate["totals"]
    aggregate_quality = aggregate["_quality"]
    meta_source = sources["meta"]
    tracker_source = sources["tracker"]
    meta_as_of = meta_source.get("last_event_at")
    tracker_as_of = tracker_source.get("last_event_at")
    meta_age = _age(now, meta_as_of)
    tracker_age = _age(now, tracker_as_of)
    tracker_status = tracker_source.get("status")
    tracker_available = tracker_status == "good" and tracker_as_of is not None

    economy_issues: list[OperatorIssue] = []
    economy_points: list[OperatorSpendPoint] = []
    final_base: str | None = None
    final_stop: str | None = None
    if not cabinet_days.timezone_known:
        missing = ", ".join(f"act_{value}" for value in cabinet_days.missing_account_ids)
        economy_issues.append(
            OperatorIssue(
                code="cabinet_timezone_unknown",
                title="Границы суток кабинета являются оценочными",
                detail=(
                    f"Нет валидного IANA timezone для: {missing}."
                    if missing
                    else "Не найден активный кабинет с подтверждённым IANA timezone."
                ),
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    if aggregate_quality["has_partial_rows"]:
        economy_issues.append(
            OperatorIssue(
                code="performance_projection_partial",
                title="Расход подтверждён не для всех строк",
                detail="Неполные Meta-снимки оставлены unknown и не заменены нулями.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    if is_live:
        for point in raw_series:
            incomplete_budget = int(point.get("unavailable_ads") or 0) > 0
            economy_points.append(
                OperatorSpendPoint(
                    at=point["ts"],
                    actual=_money(point.get("actual")),
                    base=None if incomplete_budget else _money(point.get("base")),
                    stop=None if incomplete_budget else _money(point.get("stop")),
                )
            )
        if economy_points:
            last = economy_points[-1]
            final_base, final_stop = last.base, last.stop
        if any(point.base is None for point in economy_points):
            economy_issues.append(
                OperatorIssue(
                    code="budget_threshold_partial",
                    title="Пороги рассчитаны не для всех объявлений",
                    detail="У части объявлений не задан оффер или CPA.",
                    severity=OperatorSeverity.WARNING,
                    correlation_id=None,
                )
            )
    else:
        economy_points = [
            OperatorSpendPoint(
                at=point["day"],
                actual=_money(point.get("spend")),
                base=None,
                stop=None,
            )
            for point in raw_series
        ]
        economy_issues.append(
            OperatorIssue(
                code="historical_budget_not_applicable",
                title="Base/stop доступны только за текущие сутки кабинета",
                detail=None,
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )

    spend = _money(totals.get("spend")) if meta_as_of else None
    base_delta = (
        _money(Decimal(spend) - Decimal(final_base))
        if spend is not None and final_base is not None
        else None
    )
    economy_data = OperatorEconomyData(
        totals=OperatorEconomyTotals(
            spend=spend,
            base=final_base,
            stop=final_stop,
            base_delta=base_delta,
        ),
        series=economy_points,
    )
    if meta_as_of is None:
        economy_state = DataState.UNAVAILABLE
        economy_data_value = None
        economy_issues.append(
            OperatorIssue(
                code="meta_snapshot_missing",
                title="Нет подтверждённого снимка Meta",
                detail=None,
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    elif meta_age is not None and meta_age > 60:
        economy_state = DataState.STALE
        economy_data_value = economy_data
        economy_issues.append(
            OperatorIssue(
                code="meta_snapshot_stale",
                title="Снимок Meta устарел",
                detail=f"Возраст снимка: {meta_age} с.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    elif economy_issues:
        economy_state = DataState.PARTIAL
        economy_data_value = economy_data
    else:
        economy_state = DataState.READY
        economy_data_value = economy_data

    clicks_raw = totals.get("clicks")
    registrations_raw = totals.get("registrations")
    ftd_raw = totals.get("ftds")
    confirmed_raw = totals.get("confirmed_deposits")
    clicks = int(clicks_raw) if meta_as_of and clicks_raw is not None else None
    registrations = (
        int(registrations_raw) if tracker_available and registrations_raw is not None else None
    )
    ftd = int(ftd_raw) if tracker_available and ftd_raw is not None else None
    confirmed = int(confirmed_raw) if tracker_available and confirmed_raw is not None else None
    funnel = OperatorFunnelData(
        stages=[
            OperatorFunnelStage(
                key="clicks",
                label="Клики",
                count=clicks,
                conversion=None,
                cost=_cost(spend, clicks),
            ),
            OperatorFunnelStage(
                key="registrations",
                label="Регистрации",
                count=registrations,
                conversion=_ratio(registrations, clicks),
                cost=_cost(spend, registrations),
            ),
            OperatorFunnelStage(
                key="ftd",
                label="FTD",
                count=ftd,
                conversion=_ratio(ftd, registrations),
                cost=_cost(spend, ftd),
            ),
            OperatorFunnelStage(
                key="confirmed_deposits",
                label="Подтверждённые депозиты",
                count=confirmed,
                conversion=_ratio(confirmed, ftd),
                cost=_cost(spend, confirmed),
            ),
        ]
    )
    funnel_issues: list[OperatorIssue] = []
    if aggregate_quality["has_partial_rows"]:
        funnel_issues.append(
            OperatorIssue(
                code="funnel_projection_partial",
                title="Воронка подтверждена не для всех строк",
                detail="Пропущенные значения оставлены unknown и не заменены нулями.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    if not cabinet_days.timezone_known:
        funnel_issues.append(
            OperatorIssue(
                code="cabinet_timezone_unknown",
                title="Воронка рассчитана по оценочным границам суток",
                detail="До подтверждения IANA timezone значения не считаются точными.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    if tracker_status == "degraded":
        funnel_issues.append(
            OperatorIssue(
                code="tracker_source_degraded",
                title="Данные трекера неполные или устарели",
                detail=tracker_source.get("note"),
                severity=OperatorSeverity.WARNING,
                correlation_id=None,
            )
        )
    if meta_as_of is None:
        funnel_state = DataState.UNAVAILABLE
        funnel_value = None
    elif (meta_age is not None and meta_age > 60) or tracker_status == "degraded":
        funnel_state = DataState.STALE
        funnel_value = funnel
        funnel_issues.append(
            OperatorIssue(
                code="funnel_source_stale",
                title="Один из источников воронки устарел",
                detail=(
                    f"Meta: {meta_age if meta_age is not None else 'unknown'} с; "
                    f"tracker: {tracker_age if tracker_age is not None else 'unknown'} с."
                ),
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    elif not tracker_available:
        funnel_state = DataState.PARTIAL
        funnel_value = funnel
        funnel_issues.append(
            OperatorIssue(
                code="tracker_freshness_unknown",
                title="Конверсии не подтверждены трекером",
                detail="Clicks доступны из Meta; registrations, FTD и deposits оставлены unknown.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    elif funnel_issues:
        funnel_state = DataState.PARTIAL
        funnel_value = funnel
    else:
        funnel_state = DataState.READY
        funnel_value = funnel

    return (
        OperatorSection(
            state=economy_state,
            as_of=meta_as_of,
            freshness_seconds=meta_age,
            sources=["meta", "offer_rules"],
            issues=economy_issues,
            data=economy_data_value,
        ),
        OperatorSection(
            state=funnel_state,
            as_of=min([value for value in (meta_as_of, tracker_as_of) if value], default=None),
            freshness_seconds=max(
                [
                    age
                    for age in (_age(now, meta_as_of), _age(now, tracker_as_of))
                    if age is not None
                ],
                default=None,
            ),
            sources=["meta", "adsetpro"],
            issues=funnel_issues,
            data=funnel_value,
        ),
        tracker_available,
        from_dt,
        to_dt,
        cabinet_days,
    )


async def _portfolio_section(
    *,
    engine: Any,
    account_id: str | None,
    window_name: Literal["today", "24h", "7d", "30d"],
    now: datetime,
) -> OperatorSection[OperatorPortfolioData]:
    requested_id = account_id.removeprefix("act_") if account_id else None
    if requested_id:
        account_ids = [requested_id]
    else:
        scan_ids, catalog_days = await asyncio.gather(
            resolve_configured_ad_account_ids(engine),
            resolve_cabinet_days(engine, now=now),
        )
        account_ids = sorted({*scan_ids, *catalog_days.account_ids})

    async def build_row(cabinet_id: str) -> OperatorCabinetLedgerRow:
        analytics_task = _analytics_sections(
            engine=engine,
            account_id=cabinet_id,
            window_name=window_name,
            now=now,
        )
        currency_task = resolve_account_currencies(
            engine,
            account_ids=[cabinet_id],
            now=now,
        )
        (
            (economy, funnel, _tracker_available, from_dt, to_dt, cabinet_days),
            currencies,
        ) = await asyncio.gather(analytics_task, currency_task)
        economy, _ = _fail_closed_snapshot_money(
            economy=economy,
            funnel=funnel,
            currencies=currencies,
        )
        totals = (
            economy.data.totals
            if economy.data is not None
            else OperatorEconomyTotals(spend=None, base=None, stop=None, base_delta=None)
        )
        currency = currencies.currency
        severity, risk_label, risk_reason = _cabinet_risk(
            state=economy.state,
            totals=totals,
            issues=economy.issues,
            currency=currency,
        )
        timezone_name = cabinet_days.timezone_names.get(cabinet_id)
        cabinet_day = None
        if timezone_name is not None:
            cabinet_day = {
                "starts_at": from_dt,
                "ends_at": (
                    cabinet_day_end_for_timezone(timezone_name, now)
                    if window_name == "today"
                    else to_dt
                ),
            }
        return OperatorCabinetLedgerRow(
            id=cabinet_id,
            name=f"act_{cabinet_id}",
            timezone=timezone_name,
            currency=currency,
            state=economy.state,
            severity=severity,
            as_of=economy.as_of,
            freshness_seconds=economy.freshness_seconds,
            cabinet_day=cabinet_day,
            totals=totals,
            risk_label=risk_label,
            risk_reason=risk_reason,
            issues=economy.issues,
            action=OperatorAttentionAction(
                label="Открыть кабинет",
                href=f"/cabinets/{cabinet_id}",
            ),
        )

    cabinets = list(await asyncio.gather(*(build_row(value) for value in account_ids)))
    groups = _currency_groups(cabinets)
    states = [cabinet.state for cabinet in cabinets]
    issues: list[OperatorIssue] = []
    unknown_currency_count = sum(cabinet.currency is None for cabinet in cabinets)
    non_usd_count = sum(
        cabinet.currency is not None and cabinet.currency != "USD" for cabinet in cabinets
    )
    if unknown_currency_count:
        issues.append(
            OperatorIssue(
                code="portfolio_currency_unknown",
                title="Не для всех кабинетов подтверждена валюта",
                detail=f"Кабинетов без свежего currency evidence: {unknown_currency_count}.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    if non_usd_count:
        issues.append(
            OperatorIssue(
                code="portfolio_currency_not_usd",
                title="Есть кабинеты не в USD",
                detail=f"Денежные суммы скрыты для кабинетов: {non_usd_count}.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    section_state = _combined_data_state(states)
    if (unknown_currency_count or non_usd_count) and section_state in {
        DataState.READY,
        DataState.EMPTY,
    }:
        section_state = DataState.PARTIAL
    return OperatorSection(
        state=section_state,
        as_of=min(
            (cabinet.as_of for cabinet in cabinets if cabinet.as_of is not None),
            default=None,
        ),
        freshness_seconds=max(
            (
                cabinet.freshness_seconds
                for cabinet in cabinets
                if cabinet.freshness_seconds is not None
            ),
            default=None,
        ),
        sources=["meta", "offer_rules", "meta_account_snapshot"],
        issues=issues,
        data=OperatorPortfolioData(currency_groups=groups),
    )


async def _system_section(
    *,
    engine: Any,
    now: datetime,
    account_id: str | None = None,
) -> OperatorSection[OperatorSystemData]:
    requested_id = canonical_account_id(account_id) if account_id else None
    scan = await fetch_operator_scan_state(engine, account_id=requested_id)
    expected_accounts = (
        [requested_id] if requested_id else await resolve_configured_ad_account_ids(engine)
    )
    issues: list[OperatorIssue] = []
    workers: list[OperatorWorkerState] = []
    actors = [
        actor
        for actor in scan.get("actors", [])
        if requested_id is None
        or str(actor.get("ad_account_id") or "").removeprefix("act_") == requested_id
    ]
    for actor in actors:
        actor_account_id = str(actor.get("ad_account_id") or "unknown").removeprefix("act_")
        activities = [
            value
            for key in ("last_progress_at", "last_snapshot_at")
            if isinstance((value := actor.get(key)), datetime)
        ]
        last_activity = max(activities, default=None)
        activity_age = _age(now, last_activity)
        error = actor.get("error")
        stage = str(actor.get("stage") or "unknown")
        lease_expires_at = actor.get("lease_expires_at")
        has_expired_active_lease = (
            actor.get("owner_instance") is not None
            and isinstance(lease_expires_at, datetime)
            and lease_expires_at <= now
        )
        if error or has_expired_active_lease:
            severity = OperatorSeverity.CRITICAL
            status = "failed"
        elif activity_age is None:
            severity = OperatorSeverity.UNKNOWN
            status = "unknown"
        elif activity_age > 60:
            severity = OperatorSeverity.CRITICAL
            status = "stale"
        elif stage in {"timeout", "error"}:
            severity = OperatorSeverity.CRITICAL
            status = "failed"
        elif stage == "scanning":
            severity = OperatorSeverity.WARNING
            status = "running"
        else:
            severity = OperatorSeverity.OK
            status = "online"
        workers.append(
            OperatorWorkerState(
                id=f"observer:{actor_account_id}",
                label=f"Observer · {actor_account_id}",
                severity=severity,
                status=status,
                last_activity_at=last_activity,
            )
        )
        if error:
            issues.append(
                OperatorIssue(
                    code="cabinet_actor_error",
                    title=f"Cabinet {actor_account_id}: scan actor завершился с ошибкой",
                    detail="Scan actor сообщил внутреннюю ошибку; проверьте источник данных.",
                    severity=OperatorSeverity.CRITICAL,
                    correlation_id=None,
                )
            )
        elif has_expired_active_lease:
            issues.append(
                OperatorIssue(
                    code="cabinet_actor_lease_expired",
                    title=f"Cabinet {actor_account_id}: lease истёк во время работы",
                    detail=stage,
                    severity=OperatorSeverity.CRITICAL,
                    correlation_id=None,
                )
            )
        elif activity_age is None or activity_age > 60:
            issues.append(
                OperatorIssue(
                    code="cabinet_actor_stale",
                    title=f"Cabinet {actor_account_id}: actor не обновляет состояние",
                    detail=None if activity_age is None else f"Возраст: {activity_age} с.",
                    severity=OperatorSeverity.UNKNOWN,
                    correlation_id=None,
                )
            )

    # При выключенном сканировании cabinet_runtime пуст по определению — актёров
    # никто не создаёт. Отсутствующие строки runtime здесь не являются ошибкой.
    if scan.get("enabled") is not False:
        known_accounts = {worker.id.removeprefix("observer:") for worker in workers}
        for account_id in sorted(set(expected_accounts) - known_accounts):
            workers.append(
                OperatorWorkerState(
                    id=f"observer:{account_id}",
                    label=f"Observer · {account_id}",
                    severity=OperatorSeverity.UNKNOWN,
                    status="unknown",
                    last_activity_at=None,
                )
            )
            issues.append(
                OperatorIssue(
                    code="cabinet_runtime_missing",
                    title=f"Cabinet {account_id}: actor ещё не подтверждён",
                    detail="В PostgreSQL нет runtime snapshot для настроенного кабинета.",
                    severity=OperatorSeverity.UNKNOWN,
                    correlation_id=None,
                )
            )

        if not workers:
            issues.append(
                OperatorIssue(
                    code="cabinet_runtime_missing",
                    title="Состояние cabinet actors ещё не подтверждено",
                    detail="PostgreSQL пока не содержит ни одного cabinet_runtime snapshot.",
                    severity=OperatorSeverity.UNKNOWN,
                    correlation_id=None,
                )
            )
    if scan.get("enabled") is True and not expected_accounts:
        issues.append(
            OperatorIssue(
                code="scan_accounts_missing",
                title="Нет настроенных кабинетов для scan",
                detail="Добавьте числовой ad account в активный оффер.",
                severity=OperatorSeverity.CRITICAL,
                correlation_id=None,
            )
        )

    # Одиннадцать фоновых воркеров (issue #176): их heartbeat раньше жил
    # только как process-local метрика Prometheus и никогда не попадал в
    # операторский снимок. «Не отвечает» (heartbeat не тикает) и «отвечает,
    # но простаивает» (heartbeat тикает, реальный рабочий цикл тоже — просто
    # очередь пуста) — разные состояния; см. poll_stale_after_seconds.
    try:
        heartbeat_rows = {
            str(row.get("worker_name")): row for row in await fetch_worker_heartbeats(engine)
        }
    except Exception:  # noqa: BLE001 — read-side asymmetry with the write side's
        # best-effort contract (core/worker_liveness.record_worker_heartbeat):
        # a broken/missing worker_heartbeats table must degrade this ONE
        # section to "unknown per worker", not take down the whole operator
        # snapshot (economy/portfolio/actions/...) with it (review issue #176 Л3).
        logger.warning("fetch_worker_heartbeats failed; background workers unknown", exc_info=True)
        heartbeat_rows = {}
    background_workers: list[OperatorWorkerState] = []
    for worker_name in sorted(WORKER_POLL_INTERVAL_SECONDS):
        label = _BACKGROUND_WORKER_LABELS.get(worker_name, worker_name)
        row = heartbeat_rows.get(worker_name)
        if row is None:
            background_workers.append(
                OperatorWorkerState(
                    id=f"worker:{worker_name}",
                    label=label,
                    severity=OperatorSeverity.UNKNOWN,
                    status="unknown",
                    last_activity_at=None,
                )
            )
            issues.append(
                OperatorIssue(
                    code="background_worker_missing",
                    title=f"{label}: ещё не подтверждён",
                    detail="В PostgreSQL нет heartbeat для этого воркера.",
                    severity=OperatorSeverity.UNKNOWN,
                    correlation_id=None,
                )
            )
            continue
        heartbeat_at = row.get("last_heartbeat_at")
        poll_at = row.get("last_poll_success_at")
        heartbeat_age = _age(now, heartbeat_at)
        poll_age = _age(now, poll_at)
        if heartbeat_age is None or heartbeat_age > heartbeat_stale_after_seconds():
            worker_severity = OperatorSeverity.CRITICAL
            worker_status = "offline"
        elif poll_age is None:
            # Процесс жив (heartbeat свежий), но рабочий цикл ЕЩЁ НИ РАЗУ не
            # подтвердил опрос — например, только что поднялся после деплоя:
            # у health_watchdog STARTUP_GRACE_SECONDS перед первой проверкой
            # гарантированно ≥ 90с, и каждый деплой перезапускает все 11
            # воркеров разом. Это неизвестно, а не отказ — null не равен нулю.
            worker_severity = OperatorSeverity.UNKNOWN
            worker_status = "unknown"
        elif poll_age > poll_stale_after_seconds(worker_name):
            # Рабочий цикл — тот, что реально разбирает очередь/выполняет
            # плановую проверку — раньше подтверждался, а теперь нет. Ровно
            # этот разрыв скрыл инцидент 18.08: у campaign_creator heartbeat
            # шёл из отдельной корутины, не зависящей от зависшего task_loop.
            worker_severity = OperatorSeverity.CRITICAL
            worker_status = "stalled"
        else:
            worker_severity = OperatorSeverity.OK
            worker_status = "online"
        last_worker_activity = max(
            (value for value in (heartbeat_at, poll_at) if isinstance(value, datetime)),
            default=None,
        )
        background_workers.append(
            OperatorWorkerState(
                id=f"worker:{worker_name}",
                label=label,
                severity=worker_severity,
                status=worker_status,
                last_activity_at=last_worker_activity,
            )
        )
        if worker_status == "offline":
            issues.append(
                OperatorIssue(
                    code="background_worker_offline",
                    title=f"{label}: не отвечает",
                    detail=None
                    if heartbeat_age is None
                    else f"Возраст heartbeat: {heartbeat_age} с.",
                    severity=OperatorSeverity.CRITICAL,
                    correlation_id=None,
                )
            )
        elif worker_status == "stalled":
            issues.append(
                OperatorIssue(
                    code="background_worker_stalled",
                    title=f"{label}: не разбирает очередь",
                    detail=f"Возраст последнего опроса: {poll_age} с.",
                    severity=OperatorSeverity.CRITICAL,
                    correlation_id=None,
                )
            )
        elif worker_status == "unknown":
            issues.append(
                OperatorIssue(
                    code="background_worker_poll_unconfirmed",
                    title=f"{label}: рабочий цикл ещё не подтверждён",
                    detail="Heartbeat свежий, но ни один опрос очереди/проверки ещё не прошёл.",
                    severity=OperatorSeverity.UNKNOWN,
                    correlation_id=None,
                )
            )

    last_scan = scan.get("last_scan_at")
    scan_age = _age(now, last_scan)
    all_workers = workers + background_workers
    critical_workers = [
        worker for worker in all_workers if worker.severity == OperatorSeverity.CRITICAL
    ]
    warning_workers = [
        worker for worker in all_workers if worker.severity == OperatorSeverity.WARNING
    ]
    unknown_workers = [
        worker for worker in all_workers if worker.severity == OperatorSeverity.UNKNOWN
    ]
    critical_issues = [issue for issue in issues if issue.severity == OperatorSeverity.CRITICAL]
    # Включённый мониторинг может фактически не покрывать ни одного объявления:
    # при одном кабинете пустой allowlist означает, что скан не выполняется вовсе
    # (см. allowlist_blocks_scan в observer). Раньше это давало outcome="empty",
    # неотличимый от «активных объявлений нет», и секция рисовалась зелёной —
    # оператор видел исправную систему, пока авто-стоп не покрывал ничего.
    nothing_monitored_reason: str | None = None
    if scan.get("enabled") is True and requested_id is None:
        nothing_monitored_reason = nothing_monitored_reason_for(
            expected_accounts, list(scan.get("campaign_ids") or [])
        )
    if scan.get("enabled") is False:
        severity = OperatorSeverity.UNKNOWN
        issues.append(
            OperatorIssue(
                code="monitoring_disabled",
                title="Автоматический мониторинг выключен",
                detail=None,
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    elif nothing_monitored_reason is not None:
        severity = OperatorSeverity.CRITICAL
        issues.append(
            OperatorIssue(
                code="scan_nothing_monitored",
                title="Мониторинг включён, но не отслеживает ни одного объявления",
                detail=nothing_monitored_reason,
                severity=OperatorSeverity.CRITICAL,
                correlation_id=None,
            )
        )
    elif critical_workers or critical_issues:
        severity = OperatorSeverity.CRITICAL
    elif scan.get("enabled") is None:
        severity = OperatorSeverity.UNKNOWN
        issues.append(
            OperatorIssue(
                code="monitoring_state_unknown",
                title="Состояние автоматического мониторинга неизвестно",
                detail="PostgreSQL не подтвердил, включён ли scan.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    elif scan.get("last_scan_outcome") in {"error", "partial"}:
        severity = OperatorSeverity.WARNING
        issues.append(
            OperatorIssue(
                code="last_scan_degraded",
                title="Последний scan не подтвердил полный снимок",
                detail=str(scan.get("last_scan_outcome")),
                severity=OperatorSeverity.WARNING,
                correlation_id=None,
            )
        )
    elif warning_workers or (scan_age is not None and scan_age > 60):
        severity = OperatorSeverity.WARNING
    elif unknown_workers or last_scan is None:
        severity = OperatorSeverity.UNKNOWN
    else:
        severity = OperatorSeverity.OK

    if last_scan is None:
        state = DataState.UNAVAILABLE
    elif scan_age is not None and scan_age > 60:
        state = DataState.STALE
        issues.append(
            OperatorIssue(
                code="scan_snapshot_stale",
                title="Scan snapshot устарел",
                detail=f"Возраст: {scan_age} с.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    elif issues:
        state = DataState.PARTIAL
    elif scan.get("last_scan_outcome") == "empty":
        state = DataState.EMPTY
    else:
        state = DataState.READY
    return OperatorSection(
        state=state,
        as_of=last_scan,
        freshness_seconds=scan_age,
        sources=["postgresql", "cabinet_runtime", "worker_heartbeats"],
        issues=issues,
        data=OperatorSystemData(
            severity=severity,
            monitoring_enabled=scan.get("enabled"),
            last_scan_at=last_scan,
            next_scan_at=scan.get("next_scan_at"),
            workers=workers,
            background_workers=background_workers,
        ),
    )


def _incident_attention_item(incident: dict[str, Any]) -> OperatorAttentionItem:
    resource_type = str(incident.get("resource_type") or "system")
    kind = (
        "ad"
        if resource_type in {"ad", "fb_ad"}
        else "campaign"
        if resource_type == "campaign"
        else "account"
        if resource_type in {"account", "ad_account"}
        else "system"
    )
    public_incident_id = _public_incident_id(incident["id"])
    title = redact_sensitive_text(incident.get("title")).strip() or "Инцидент требует проверки"
    summary = (
        redact_sensitive_text(incident.get("summary") or incident.get("title")).strip()
        or "Инцидент требует проверки"
    )
    return OperatorAttentionItem(
        id=public_incident_id,
        kind="incident",
        severity=incident["severity"],
        title=title[:240],
        summary=summary[:500],
        reason=_incident_public_reason(incident),
        occurred_at=incident["opened_at"],
        target=OperatorAttentionTarget(
            kind=kind,
            id=(
                redact_sensitive_text(incident.get("resource_id"))
                if incident.get("resource_id")
                else None
            ),
            label=(
                redact_sensitive_text(incident.get("resource_label"))[:240]
                if incident.get("resource_label")
                else None
            ),
        ),
        action=OperatorAttentionAction(label="Открыть", href=f"/incidents/{public_incident_id}"),
        recovery_action=(
            "retry_scan"
            if str(incident.get("incident_key") or "").startswith(LOGIN_REQUIRED_INCIDENT_PREFIX)
            else None
        ),
        status=incident["status"],
        requires_usd_evidence=_incident_requires_usd_evidence(incident),
    )


def _incident_requires_usd_evidence(incident: dict[str, Any]) -> bool:
    facts_value = incident.get("facts")
    facts = facts_value if isinstance(facts_value, dict) else {}
    return bool(
        incident.get("ad_account_id")
        or str(incident.get("resource_type") or "") in {"ad", "fb_ad", "campaign", "account"}
        or any(key in facts for key in ("currency", "currency_state", "metrics", "risk_ratio"))
    )


def _incident_public_reason(incident: dict[str, Any]) -> str | None:
    facts_value = incident.get("facts")
    facts = facts_value if isinstance(facts_value, dict) else {}
    direct = facts.get("risk")
    if isinstance(direct, str) and direct.strip():
        return redact_sensitive_text(direct).strip()[:240]
    card = facts.get("card")
    if isinstance(card, dict):
        nested = card.get("risk")
        if isinstance(nested, str) and nested.strip():
            return redact_sensitive_text(nested).strip()[:240]
    return None


def _currency_hidden_money_summary(currency_state: str) -> str:
    """Issue 354: заголовок инцидента — это природа сигнала, а не деньги;

    прятать имеет смысл только сумму, и только объяснив причину (mixed vs
    unknown), а не одной and-the-same строкой для обоих случаев.
    """
    if currency_state == "mixed":
        return "В выборке несколько валют. Сузьте до одного кабинета — денежные детали скрыты."
    return "Валюта кабинета не подтверждена. Обновите снимок — денежные детали скрыты."


def _incident_item(
    incident: dict[str, Any],
    *,
    currency_state: str,
) -> OperatorIncidentItem:
    resource_type = str(incident.get("resource_type") or "system")
    kind = (
        "ad"
        if resource_type in {"ad", "fb_ad"}
        else "campaign"
        if resource_type == "campaign"
        else "account"
        if resource_type == "account"
        else "system"
    )
    requires_usd_evidence = _incident_requires_usd_evidence(incident)
    # Issue 354: единственная причина скрывать сумму — отсутствие единственной
    # подтверждённой валюты (mixed/unknown). Один не-долларовый, но
    # подтверждённый кабинет — не причина: заголовок сообщает природу
    # сигнала, а не сумму, и всегда остаётся видимым.
    money_copy_visible = not requires_usd_evidence or currency_state == "single"
    raw_title = redact_sensitive_text(incident.get("title")).strip()
    raw_summary = redact_sensitive_text(incident.get("summary")).strip()
    public_incident_id = _public_incident_id(incident["id"])
    return OperatorIncidentItem(
        id=public_incident_id,
        severity=incident["severity"],
        status=incident["status"],
        title=raw_title or "Инцидент требует проверки",
        summary=(
            (raw_summary or None)
            if money_copy_visible
            else _currency_hidden_money_summary(currency_state)
        ),
        reason=_incident_public_reason(incident) if money_copy_visible else None,
        occurred_at=incident["opened_at"],
        account_id=(str(incident["ad_account_id"]) if incident.get("ad_account_id") else None),
        target=OperatorAttentionTarget(
            kind=kind,
            id=(
                redact_sensitive_text(incident.get("resource_id"))
                if incident.get("resource_id")
                else None
            ),
            label=(
                redact_sensitive_text(incident.get("resource_label"))[:240]
                if incident.get("resource_label")
                else None
            ),
        ),
        action=OperatorAttentionAction(label="Открыть", href=f"/incidents/{public_incident_id}"),
        requires_usd_evidence=requires_usd_evidence,
    )


def _attention_section(
    *,
    incidents: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    system: OperatorSection[OperatorSystemData],
    now: datetime,
    include_system_issues: bool = True,
) -> OperatorSection[OperatorAttentionData]:
    items = [_incident_attention_item(incident) for incident in incidents]
    decision_action_ids: set[str] = set()
    for action in actions:
        if action["state"] not in {"failed", "unknown", "running"}:
            continue
        severity = (
            "critical"
            if action["state"] in {"failed", "unknown"} and action["kind"] in {"pause", "activate"}
            else "warning"
        )
        if action["state"] in {"failed", "unknown"}:
            # Running-действие — прогресс, а не решение: в ленту «Решения» и в
            # её счётчик оно не входит (см. `selectDecisionRows`).
            decision_action_ids.add(f"task:{action['id']}")
        items.append(
            OperatorAttentionItem(
                id=f"task:{action['id']}",
                kind="action",
                severity=severity,
                title=action["title"],
                summary=f"{action['public_id']} · {action['state']}",
                reason=action.get("reason"),
                occurred_at=action["updated_at"],
                target=OperatorAttentionTarget(
                    kind="ad" if action["kind"] in {"pause", "activate"} else "system",
                    id=None,
                    label=action.get("target_label"),
                ),
                action=OperatorAttentionAction(label="Открыть", href=f"/actions/{action['id']}"),
                recovery_action=None,
                status=None,
                requires_usd_evidence=False,
            )
        )
    if include_system_issues:
        for issue in system.issues:
            if issue.severity == OperatorSeverity.OK:
                continue
            items.append(
                OperatorAttentionItem(
                    id=f"source:{issue.code}",
                    kind="source",
                    severity=issue.severity,
                    title=issue.title,
                    summary=issue.detail or "Откройте диагностику источников.",
                    reason=issue.code,
                    occurred_at=system.as_of or now,
                    target=OperatorAttentionTarget(kind="system", id=None, label="Система"),
                    action=OperatorAttentionAction(label="Диагностика", href="/system/sources"),
                    recovery_action=None,
                    status=None,
                    requires_usd_evidence=False,
                )
            )
    # Компаратор обязан совпадать с клиентским `compareDecisionRows`
    # (packages/shared/src/operator/decisionFeed.ts) — это то же правило,
    # которое byer утвердил в issue #338: severity → деньги раньше системного
    # → occurred_at по возрастанию (незакрытое обязательство дорожает со
    # временем, это не лента новостей) → id как детерминированный tie-break.
    # Расхождение здесь означает, что при срезе лимитом сервер отдаёт не
    # 50 важнейших строк, а 50 отобранных по другому правилу (issue #355).
    severity_rank = {"critical": 0, "unknown": 1, "warning": 2, "ok": 3}
    money_rank = {"ad": 0, "campaign": 0, "account": 0, "system": 1}
    items.sort(
        key=lambda item: (
            severity_rank[item.severity],
            money_rank[item.target.kind],
            item.occurred_at.timestamp(),
            item.id,
        )
    )
    if include_system_issues and system.state in {
        DataState.PARTIAL,
        DataState.STALE,
        DataState.UNAVAILABLE,
    }:
        state = DataState.PARTIAL
    else:
        state = DataState.READY if items else DataState.EMPTY
    attention_limit = 50
    visible_items = items[:attention_limit]
    # Правило отбора ленты «Решения» — то же, что в клиентском
    # `selectDecisionRows`: строка требует решения, а не просто сообщает о
    # происходящем.
    decision_rows = [
        item
        for item in items
        if item.kind == "incident"
        or item.id in decision_action_ids
        or (item.kind == "source" and item.severity != "ok")
    ]
    return OperatorSection(
        state=state,
        as_of=max((item.occurred_at for item in items), default=system.as_of),
        freshness_seconds=system.freshness_seconds,
        sources=["incidents", "task_queue", "worker_telemetry"],
        issues=[],
        data=OperatorAttentionData(
            items=visible_items,
            total=len(items),
            truncated=len(items) > len(visible_items),
            decisions_count=len(decision_rows),
            decisions_critical=any(row.severity == "critical" for row in decision_rows),
        ),
    )


def _approaching_stop_section(
    *,
    rows: list[dict[str, Any]],
    now: datetime,
) -> OperatorSection[OperatorApproachingStopData]:
    """Build a ranked early-warning section from persisted evaluator context.

    Issue 352: a row drops out only when Meta confirmed it isn't running
    (``DELIVERY_DISABLED_STATUSES``). An unconfirmed/unrecognized status is a
    "we don't know", not a "we know it's inactive" — dropping it would be a
    dangerous-direction false negative (a still-delivering ad silently missing
    from the early-warning feed). It stays, with severity forced to unknown
    so it never reads as a clean "ok" row while its delivery is unconfirmed.
    """
    items: list[OperatorAdRow] = []
    for row in rows:
        item = OperatorAdRow.model_validate(row)
        if item.rule_context.percent_to_stop is None or item.rule_context.stage == "stop":
            continue
        if item.severity == OperatorSeverity.CRITICAL:
            continue
        if normalized_delivery_status(item.delivery_status) in DELIVERY_DISABLED_STATUSES:
            continue
        if item.delivery_status is None and item.severity == OperatorSeverity.OK:
            item = item.model_copy(update={"severity": OperatorSeverity.UNKNOWN})
        items.append(item)
    items.sort(
        key=lambda item: (
            -Decimal(item.rule_context.percent_to_stop or "0"),
            item.id,
        )
    )
    if not items:
        return OperatorSection(
            state=DataState.EMPTY,
            as_of=now,
            freshness_seconds=0,
            sources=["postgresql", "meta", "adsetpro"],
            issues=[],
            data=OperatorApproachingStopData(items=[]),
        )
    as_of = min((item.as_of for item in items if item.as_of is not None), default=None)
    return OperatorSection(
        state=_combined_data_state([item.data_state for item in items]),
        as_of=as_of,
        freshness_seconds=_age(now, as_of),
        sources=["postgresql", "meta", "adsetpro"],
        issues=[],
        data=OperatorApproachingStopData(items=items[:50]),
    )


def _hide_unconfirmed_rule_money(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        metrics = row["metrics"]
        metrics["spend"] = None
        metrics["cpc"] = None
        metrics["cost_per_registration"] = None
        metrics["cost_per_ftd"] = None
        context = row["rule_context"]
        if context["rule_code"] in _MONEY_RULE_CODES:
            context["value"] = None
            context["threshold"] = None
            # Issue 353: процент без числителя и знаменателя не читается —
            # прячем его вместе с ними, а не оставляем голым отношением.
            context["percent_to_stop"] = None


def _redact_approaching_stop_row(item: OperatorAdRow) -> OperatorAdRow:
    """Apply the same money-hiding as `_hide_unconfirmed_rule_money`, but

    after `_approaching_stop_section` already selected and ranked rows by the
    real `percent_to_stop`. Redacting the dict first (issue #353) would null
    `percent_to_stop` and make the section's own "no percent → drop" rule
    swallow the row — reintroducing the exact silent-disappearance failure
    mode issue #352 fixes, just for a different reason.
    """
    metrics = item.metrics.model_copy(
        update={
            "spend": None,
            "cpc": None,
            "cost_per_registration": None,
            "cost_per_ftd": None,
        }
    )
    context = item.rule_context
    if context.rule_code in _MONEY_RULE_CODES:
        context = context.model_copy(
            update={"value": None, "threshold": None, "percent_to_stop": None}
        )
    return item.model_copy(update={"metrics": metrics, "rule_context": context})


async def _fetch_approaching_stop_rows(
    *,
    engine: Any,
    account_id: str | None,
    now: datetime,
) -> list[dict[str, Any]]:
    from_dt, to_dt, _, _, cabinet_days = await _window(
        engine,
        "today",
        account_id=account_id,
        now=now,
    )
    sources = await fetch_source_quality(
        engine,
        from_dt=from_dt,
        to_dt=to_dt,
        cabinet_days=cabinet_days,
        account_id=account_id,
    )
    payload = await fetch_operator_ads(
        engine,
        from_dt=from_dt,
        to_dt=to_dt,
        account_id=account_id,
        search=None,
        delivery_status=None,
        severity=None,
        sort="percent_to_stop",
        direction="desc",
        page=1,
        page_size=50,
        tracker_available=sources["tracker"].get("status") == "good",
        approaching_only=True,
    )
    return payload["rows"]


@router.get(
    "/events",
    response_model=list[OperatorEventItem],
    responses=_PROBLEM_RESPONSES,
)
async def get_operator_events(
    engine: DepEngine,
    period: Literal["today", "7d", "30d", "custom"] = Query(default="30d"),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    campaign_id: uuid.UUID | None = Query(default=None),
    fb_ad_id: str | None = Query(default=None, max_length=64),
    stage: str | None = Query(default=None, pattern="^(warning|stop)$"),
    task_status: str | None = Query(
        default=None,
        pattern="^(SUCCEEDED|FAILED|CANCELLED|succeeded|failed|cancelled)$",
    ),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[OperatorEventItem] | JSONResponse:
    """Return the bounded alert and terminal-action feed used by Analytics."""
    correlation_id = str(uuid.uuid4())
    try:
        from_dt, to_dt = _operator_events_window(period, from_date, to_date)
    except ValueError as exc:
        return _problem(
            status_code=422,
            code="invalid_events_window",
            message=str(exc),
            correlation_id=correlation_id,
        )
    try:
        rows = await fetch_operator_events(
            engine,
            from_dt=from_dt,
            to_dt=to_dt,
            limit=limit,
            campaign_uuid=campaign_id,
            fb_ad_id=fb_ad_id,
            stage=stage,
            task_status=task_status,
            search=search,
        )
    except Exception as exc:  # noqa: BLE001 - public contract must fail closed
        logger.error("operator events unavailable: %s", type(exc).__name__)
        return _problem(
            status_code=503,
            code="operator_events_unavailable",
            message="Лента событий временно недоступна",
            correlation_id=correlation_id,
        )

    events: list[OperatorEventItem] = []
    for row in rows:
        rule_codes: list[str] | None = None
        if row.rule_codes_raw:
            try:
                parsed = json.loads(row.rule_codes_raw)
            except (TypeError, json.JSONDecodeError):
                parsed = []
            rule_codes = [str(code) for code in parsed] if isinstance(parsed, list) else []
        public_task_status: str | None = None
        if row.task_status:
            try:
                public_task_status = to_frontend_task_status(row.task_status)
            except ValueError:
                public_task_status = str(row.task_status).upper()
        events.append(
            OperatorEventItem(
                event_type=row.event_type,
                ts=row.ts,
                fb_ad_id=row.fb_ad_id,
                ad_name=row.ad_name,
                campaign_id=row.campaign_id,
                campaign_name=row.campaign_name,
                stage=row.stage,
                rule_codes=rule_codes,
                task_type=row.task_type,
                task_status=public_task_status,
            )
        )
    return events


@router.get(
    "/snapshot",
    response_model=OperatorSnapshot,
    responses=_PROBLEM_RESPONSES,
)
async def get_operator_snapshot(
    engine: DepEngine,
    settings: DepSettings,
    account_id: str | None = Query(default=None, max_length=64),
    window: Literal["today", "24h", "7d", "30d"] = Query(default="today"),
    timezone: str | None = Query(default=None, min_length=1, max_length=64),
) -> OperatorSnapshot | JSONResponse:
    correlation_id = str(uuid.uuid4())
    timezone_name = timezone or settings.app_timezone
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return _problem(
            status_code=422,
            code="invalid_timezone",
            message="Неизвестный IANA timezone",
            correlation_id=correlation_id,
        )
    now = datetime.now(UTC)
    try:
        analytics_task = _analytics_sections(
            engine=engine,
            account_id=account_id,
            window_name=window,
            now=now,
        )
        portfolio_task = _portfolio_section(
            engine=engine,
            account_id=account_id,
            window_name=window,
            now=now,
        )
        system_task = _system_section(engine=engine, now=now, account_id=account_id)
        actions_task = fetch_operator_actions(engine, limit=20, account_id=account_id)
        incidents_task = fetch_operator_incidents(engine, account_id=account_id, limit=50)
        approaching_stop_task = _fetch_approaching_stop_rows(
            engine=engine,
            account_id=account_id,
            now=now,
        )
        revision_task = fetch_operator_revision(engine)
        account_task = _account_meta(engine, account_id)
        currency_task = resolve_account_currencies(
            engine,
            account_ids=[account_id] if account_id else None,
        )
        (
            (economy, funnel, _tracker_available, from_dt, to_dt, cabinet_days),
            portfolio,
            system,
            (
                action_rows,
                _next_cursor,
                actions_as_of,
            ),
            incidents,
            approaching_stop_rows,
            (sequence, revision),
            account,
            currencies,
        ) = await asyncio.gather(
            analytics_task,
            portfolio_task,
            system_task,
            actions_task,
            incidents_task,
            approaching_stop_task,
            revision_task,
            account_task,
            currency_task,
        )
    except Exception:  # noqa: BLE001
        logger.exception("operator snapshot failed correlation_id=%s", correlation_id)
        return _problem(
            status_code=503,
            code="operator_snapshot_unavailable",
            message="Операторский снимок временно недоступен",
            correlation_id=correlation_id,
        )
    economy, funnel = _fail_closed_snapshot_money(
        economy=economy,
        funnel=funnel,
        currencies=currencies,
    )
    action_items = [OperatorActionItem.model_validate(item) for item in action_rows]
    actions = OperatorSection(
        state=DataState.READY if action_items else DataState.EMPTY,
        as_of=actions_as_of or now,
        freshness_seconds=_age(now, actions_as_of) if actions_as_of else 0,
        sources=["task_queue"],
        issues=[],
        data=OperatorActionsData(items=action_items),
    )
    attention = _attention_section(
        incidents=incidents,
        actions=action_rows,
        system=system,
        now=now,
        include_system_issues=account_id is None,
    )
    approaching_stop = _approaching_stop_section(rows=approaching_stop_rows, now=now)
    if _currency_issue(currencies) is not None and approaching_stop.data is not None:
        approaching_stop = approaching_stop.model_copy(
            update={
                "data": OperatorApproachingStopData(
                    items=[
                        _redact_approaching_stop_row(item) for item in approaching_stop.data.items
                    ]
                )
            }
        )
    cabinet_end = to_dt
    if window == "today":
        if cabinet_days.cabinet_timezone is not None:
            cabinet_end = cabinet_day_end_for_timezone(cabinet_days.cabinet_timezone, now)
        else:
            cabinet_end = from_dt + timedelta(days=1)
    return OperatorSnapshot(
        meta=OperatorSnapshotMeta(
            revision=revision,
            sequence=sequence,
            generated_at=now,
            timezone=timezone_name,
            cabinet_timezone=cabinet_days.cabinet_timezone,
            cabinet_timezone_known=cabinet_days.timezone_known,
            cabinet_timezone_state=cabinet_days.timezone_state,
            missing_timezone_account_ids=list(cabinet_days.missing_account_ids),
            currency=currencies.currency,
            currency_state=currencies.state,
            missing_currency_account_ids=list(currencies.missing_account_ids),
            currency_observed_at=currencies.observed_at,
            window=window,
            account=account,
            cabinet_day={"starts_at": from_dt, "ends_at": cabinet_end},
        ),
        attention=attention,
        approaching_stop=approaching_stop,
        portfolio=portfolio,
        economy=economy,
        funnel=funnel,
        actions=actions,
        system=system,
    )


@router.get(
    "/cabinets/{cabinet_id}/snapshot",
    response_model=OperatorSnapshot,
    responses=_PROBLEM_RESPONSES,
)
async def get_operator_cabinet_snapshot(
    engine: DepEngine,
    settings: DepSettings,
    cabinet_id: str = Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
    window: Literal["today", "24h", "7d", "30d"] = Query(default="today"),
    timezone: str | None = Query(default=None, min_length=1, max_length=64),
) -> OperatorSnapshot | JSONResponse:
    """Return the canonical operator snapshot narrowed to one cabinet."""

    return await get_operator_snapshot(
        engine=engine,
        settings=settings,
        account_id=cabinet_id,
        window=window,
        timezone=timezone,
    )


@router.get(
    "/actions",
    response_model=OperatorActionsResponse,
    responses=_PROBLEM_RESPONSES,
)
async def get_operator_actions(
    engine: DepEngine,
    settings: DepSettings,
    account_id: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=30, ge=1, le=100),
    before_id: int | None = Query(default=None, ge=1),
    state: list[str] = Query(default_factory=list),
) -> OperatorActionsResponse | JSONResponse:
    now = datetime.now(UTC)
    correlation_id = str(uuid.uuid4())
    requested_account_id = canonical_account_id(account_id) if account_id else None
    if account_id is not None and not requested_account_id:
        return _problem(
            status_code=422,
            code="invalid_account_id",
            message="Выберите корректный рекламный кабинет",
            correlation_id=correlation_id,
        )
    account_scope = [requested_account_id] if requested_account_id else None
    try:
        (
            (items, next_cursor, as_of),
            cabinet_days,
            currencies,
        ) = await asyncio.gather(
            fetch_operator_actions(
                engine,
                limit=limit,
                before_id=before_id,
                states=tuple(state),
                account_id=requested_account_id,
            ),
            resolve_cabinet_days(engine, account_ids=account_scope),
            resolve_account_currencies(engine, account_ids=account_scope),
        )
    except Exception:  # noqa: BLE001
        logger.exception("operator actions failed correlation_id=%s", correlation_id)
        return _problem(
            status_code=503,
            code="operator_actions_unavailable",
            message="История действий временно недоступна",
            correlation_id=correlation_id,
        )
    return OperatorActionsResponse(
        state=DataState.READY if items else DataState.EMPTY,
        as_of=as_of or now,
        freshness_seconds=_age(now, as_of) if as_of else 0,
        sources=["postgresql"],
        issues=[],
        scope=_scope_evidence(
            cabinet_days=cabinet_days,
            currencies=currencies,
            display_timezone=settings.app_timezone,
        ),
        items=[OperatorActionItem.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@router.get(
    "/ads",
    response_model=OperatorAdsResponse,
    responses=_PROBLEM_RESPONSES,
)
async def get_operator_ads(
    engine: DepEngine,
    settings: DepSettings,
    account_id: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=200),
    delivery_status: str | None = Query(default=None, max_length=64),
    severity: Literal["ok", "warning", "critical", "unknown"] | None = Query(default=None),
    sort: Literal[
        "name",
        "spend",
        "clicks",
        "registrations",
        "ftd",
        "updated",
        # Порядок по близости к стопу считает БД: клиент видит только текущую
        # страницу, и ранжирование внутри неё вводит в заблуждение — самое
        # опасное объявление может лежать на следующей.
        "percent_to_stop",
    ] = Query(default="updated"),
    direction: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
) -> OperatorAdsResponse | JSONResponse:
    now = datetime.now(UTC)
    correlation_id = str(uuid.uuid4())
    try:
        from_dt, to_dt, _, _, cabinet_days = await _window(
            engine,
            "today",
            account_id=account_id,
            now=now,
        )
        sources = await fetch_source_quality(
            engine,
            from_dt=from_dt,
            to_dt=to_dt,
            cabinet_days=cabinet_days,
            account_id=account_id,
        )
        currencies = await resolve_account_currencies(
            engine,
            account_ids=list(cabinet_days.account_ids),
        )
        if sort == "spend" and currencies.state != "single":
            return _problem(
                status_code=422,
                code="money_sort_requires_single_currency",
                message="Для сортировки по расходу выберите кабинет с подтверждённой валютой",
                correlation_id=correlation_id,
            )
        meta_source = sources["meta"]
        meta_as_of = meta_source.get("last_event_at")
        tracker_available = sources["tracker"].get("status") == "good"
        payload = await fetch_operator_ads(
            engine,
            from_dt=from_dt,
            to_dt=to_dt,
            account_id=account_id,
            search=search,
            delivery_status=delivery_status,
            severity=severity,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
            tracker_available=tracker_available,
        )
    except Exception:  # noqa: BLE001
        logger.exception("operator ads failed correlation_id=%s", correlation_id)
        return _problem(
            status_code=503,
            code="operator_ads_unavailable",
            message="Список объявлений временно недоступен",
            correlation_id=correlation_id,
        )
    as_of = payload["as_of"] or (meta_as_of if payload["total"] == 0 else None)
    freshness = _age(now, as_of)
    meta_freshness = _age(now, meta_as_of)
    issues: list[OperatorIssue] = []
    state_value = _ads_section_state(
        meta_as_of=meta_as_of,
        meta_freshness=meta_freshness,
        meta_status=meta_source.get("status"),
        row_state=payload["row_state"],
        total=payload["total"],
        timezone_known=cabinet_days.timezone_known,
        tracker_available=tracker_available,
    )
    if payload["row_state"] == DataState.UNAVAILABLE:
        issues.append(
            OperatorIssue(
                code="ad_metrics_unavailable",
                title="Метрики объявлений не подтверждены",
                detail="Для всех строк в выборке отсутствует снимок Meta.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    elif payload["row_state"] == DataState.PARTIAL:
        issues.append(
            OperatorIssue(
                code="ad_metrics_partial",
                title="Метрики доступны не для всех объявлений",
                detail="В выборке есть устаревшие строки или строки без снимка Meta.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    elif payload["row_state"] == DataState.STALE:
        issues.append(
            OperatorIssue(
                code="ad_metrics_stale",
                title="Снимки объявлений устарели",
                detail="Ни одна строка в выборке не обновлялась за последние 60 секунд.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    if meta_freshness is not None and meta_freshness > 60:
        issues.append(
            OperatorIssue(
                code="meta_source_stale",
                title="Источник Meta устарел",
                detail=f"Возраст последнего снимка: {meta_freshness} с.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    if not cabinet_days.timezone_known:
        issues.append(
            OperatorIssue(
                code="cabinet_timezone_unknown",
                title="Spend рассчитан по оценочной границе суток",
                detail="До подтверждения IANA timezone значения не считаются точными.",
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    if not tracker_available:
        issues.append(
            OperatorIssue(
                code="tracker_freshness_unknown",
                title="Конверсии в строках оставлены unknown",
                detail=None,
                severity=OperatorSeverity.UNKNOWN,
                correlation_id=None,
            )
        )
    currency_issue = _currency_issue(currencies)
    if currency_issue is not None:
        issues.append(currency_issue)
        if payload["rows"]:
            state_value = DataState.PARTIAL
            for row in payload["rows"]:
                row["data_state"] = DataState.PARTIAL
            _hide_unconfirmed_rule_money(payload["rows"])
    return OperatorAdsResponse(
        state=state_value,
        as_of=as_of,
        freshness_seconds=freshness,
        sources=["meta", "adsetpro"],
        issues=issues,
        scope=_scope_evidence(
            cabinet_days=cabinet_days,
            currencies=currencies,
            display_timezone=settings.app_timezone,
        ),
        rows=payload["rows"],
        page=page,
        page_size=page_size,
        total=payload["total"],
        pages=payload["pages"],
    )


async def _enqueue_operator_command(
    *,
    action: Literal["pause_ad", "activate_ad"],
    ad_id: str,
    engine: Any,
    idempotency_key: str,
    requested_by: str,
    response: Response,
    precondition: OperatorAdCommandRequest,
) -> OperatorCommandResponse | JSONResponse:
    correlation_id = str(uuid.uuid4())
    try:
        scoped_idempotency_key = principal_scoped_idempotency_key(
            principal=requested_by,
            client_key=idempotency_key,
        )
        receipt = await CommandService(engine).enqueue_ad_action(
            action_kind=action,
            fb_ad_id=ad_id,
            requested_by=requested_by,
            idempotency_key=scoped_idempotency_key,
            expected_delivery_status=precondition.expected_delivery_status,
            expected_as_of=precondition.expected_as_of,
        )
    except CommandNotFoundError:
        return _problem(
            status_code=404,
            code="ad_not_found",
            message="Объявление не найдено",
            correlation_id=correlation_id,
        )
    except CommandConflictError:
        return _problem(
            status_code=409,
            code="idempotency_conflict",
            message="Idempotency-Key уже связан с другим действием",
            correlation_id=correlation_id,
        )
    except CommandPreconditionError:
        return _problem(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            code="command_precondition_failed",
            message="Данные объявления изменились. Обновите карточку и повторите действие.",
            correlation_id=correlation_id,
        )
    except ValueError as exc:
        return _problem(
            status_code=422,
            code="invalid_command",
            message=str(exc),
            correlation_id=correlation_id,
        )
    response.status_code = (
        status.HTTP_202_ACCEPTED if receipt.state == "queued" else status.HTTP_200_OK
    )
    return OperatorCommandResponse(
        task_id=receipt.task_id,
        public_id=f"#{receipt.task_id}",
        state=receipt.state,
        created=receipt.created,
        correlation_id=_public_request_id(receipt.correlation_id),
    )


@router.post(
    "/scan/retry",
    response_model=OperatorCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_COMMAND_RESPONSES,
)
async def retry_operator_scan(
    engine: DepEngine,
    response: Response,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    requested_by: str = Header(default="operator:web", alias="X-Operator-Principal", max_length=64),
) -> OperatorCommandResponse | JSONResponse:
    """Queue an interactive recovery scan; 202 never means scan success."""
    correlation_id = str(uuid.uuid4())
    principal = getattr(request.state, "operator_principal", requested_by)
    try:
        scoped_idempotency_key = principal_scoped_idempotency_key(
            principal=principal,
            client_key=idempotency_key,
        )
        receipt = await CommandService(engine).enqueue_scan_retry(
            requested_by=principal,
            idempotency_key=scoped_idempotency_key,
        )
    except CommandConflictError:
        return _problem(
            status_code=409,
            code="idempotency_conflict",
            message="Idempotency-Key уже связан с другим действием",
            correlation_id=correlation_id,
        )
    except ValueError as exc:
        return _problem(
            status_code=422,
            code="invalid_command",
            message=str(exc),
            correlation_id=correlation_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("operator retry scan failed correlation_id=%s", correlation_id)
        return _problem(
            status_code=503,
            code="scan_retry_unavailable",
            message="Повторный скан временно недоступен",
            correlation_id=correlation_id,
        )

    response.status_code = (
        status.HTTP_202_ACCEPTED if receipt.state == "queued" else status.HTTP_200_OK
    )
    return OperatorCommandResponse(
        task_id=receipt.task_id,
        public_id=f"#{receipt.task_id}",
        state=receipt.state,
        created=receipt.created,
        correlation_id=_public_request_id(receipt.correlation_id),
    )


@router.post(
    "/ads/{ad_id}/pause",
    response_model=OperatorCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_COMMAND_RESPONSES,
)
async def pause_operator_ad(
    ad_id: str,
    body: OperatorAdCommandRequest,
    engine: DepEngine,
    response: Response,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    requested_by: str = Header(default="operator:web", alias="X-Operator-Principal", max_length=64),
) -> OperatorCommandResponse | JSONResponse:
    return await _enqueue_operator_command(
        action="pause_ad",
        ad_id=ad_id,
        engine=engine,
        idempotency_key=idempotency_key,
        requested_by=getattr(request.state, "operator_principal", requested_by),
        response=response,
        precondition=body,
    )


@router.post(
    "/ads/{ad_id}/activate",
    response_model=OperatorCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_COMMAND_RESPONSES,
)
async def activate_operator_ad(
    ad_id: str,
    body: OperatorAdCommandRequest,
    engine: DepEngine,
    response: Response,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    requested_by: str = Header(default="operator:web", alias="X-Operator-Principal", max_length=64),
) -> OperatorCommandResponse | JSONResponse:
    return await _enqueue_operator_command(
        action="activate_ad",
        ad_id=ad_id,
        engine=engine,
        idempotency_key=idempotency_key,
        requested_by=getattr(request.state, "operator_principal", requested_by),
        response=response,
        precondition=body,
    )


@router.get(
    "/incidents",
    response_model=OperatorIncidentsResponse,
    responses=_PROBLEM_RESPONSES,
)
async def get_operator_incidents(
    engine: DepEngine,
    settings: DepSettings,
    account_id: str | None = Query(default=None, min_length=1, max_length=64),
    severity: list[Literal["ok", "warning", "critical", "unknown"]] = Query(default_factory=list),
    incident_status: list[
        Literal["open", "acknowledged", "executing", "resolved", "failed"]
    ] = Query(default_factory=list, alias="status"),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=30, ge=10, le=100),
) -> OperatorIncidentsResponse | JSONResponse:
    """Return the complete incident journal with explicit cabinet evidence."""

    now = datetime.now(UTC)
    correlation_id = str(uuid.uuid4())
    requested_account_id = canonical_account_id(account_id) if account_id else None
    if account_id is not None and (
        not requested_account_id
        or not requested_account_id.isascii()
        or not requested_account_id.isdigit()
    ):
        return _problem(
            status_code=422,
            code="invalid_account_id",
            message="Выберите корректный рекламный кабинет",
            correlation_id=correlation_id,
        )
    account_scope = [requested_account_id] if requested_account_id else None
    try:
        (incident_rows, total), cabinet_days, currencies = await asyncio.gather(
            fetch_operator_incident_page(
                engine,
                account_id=requested_account_id,
                severities=tuple(severity),
                statuses=tuple(incident_status),
                page=page,
                page_size=page_size,
            ),
            resolve_cabinet_days(engine, account_ids=account_scope, now=now),
            resolve_account_currencies(engine, account_ids=account_scope, now=now),
        )
    except Exception:  # noqa: BLE001
        logger.exception("operator incidents failed correlation_id=%s", correlation_id)
        return _problem(
            status_code=503,
            code="operator_incidents_unavailable",
            message="Журнал инцидентов временно недоступен",
            correlation_id=correlation_id,
        )

    currency_confirmed = currencies.state == "single"
    items = [_incident_item(row, currency_state=currencies.state) for row in incident_rows]
    has_suppressed_money = any(
        item.requires_usd_evidence and not currency_confirmed for item in items
    )
    issues: list[OperatorIssue] = []
    if has_suppressed_money:
        currency_issue = _currency_issue(currencies)
        if currency_issue is not None:
            issues.append(currency_issue)
    state_value = (
        DataState.EMPTY
        if total == 0
        else DataState.PARTIAL
        if has_suppressed_money
        else DataState.READY
    )
    return OperatorIncidentsResponse(
        state=state_value,
        as_of=now,
        freshness_seconds=0,
        sources=["incidents", "meta_account_snapshot"],
        issues=issues,
        scope=_scope_evidence(
            cabinet_days=cabinet_days,
            currencies=currencies,
            display_timezone=settings.app_timezone,
        ),
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size if total else 0,
    )


@router.get(
    "/incidents/{incident_id}",
    response_model=OperatorIncidentDetailResponse,
    responses=_PROBLEM_RESPONSES,
)
async def get_operator_incident(
    incident_id: str,
    engine: DepEngine,
    settings: DepSettings,
) -> OperatorIncidentDetailResponse | JSONResponse:
    correlation_id = str(uuid.uuid4())
    try:
        internal_incident_id = parse_public_uuid(incident_id, prefix="inc")
    except ValueError:
        return _problem(
            status_code=404,
            code="incident_not_found",
            message="Инцидент не найден",
            correlation_id=correlation_id,
        )
    now = datetime.now(UTC)
    try:
        incident = await fetch_operator_incident(engine, incident_id=internal_incident_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "operator incident detail failed correlation_id=%s",
            correlation_id,
        )
        return _problem(
            status_code=503,
            code="incident_detail_unavailable",
            message="Инцидент временно недоступен",
            correlation_id=correlation_id,
        )
    if incident is None:
        return _problem(
            status_code=404,
            code="incident_not_found",
            message="Инцидент не найден",
            correlation_id=correlation_id,
        )

    account_id = str(incident.get("ad_account_id") or "").strip().removeprefix("act_")
    account_scope = [account_id] if account_id else None
    try:
        cabinet_days, currencies = await asyncio.gather(
            resolve_cabinet_days(engine, account_ids=account_scope, now=now),
            resolve_account_currencies(engine, account_ids=account_scope, now=now),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "operator incident evidence failed correlation_id=%s",
            correlation_id,
        )
        return _problem(
            status_code=503,
            code="incident_evidence_unavailable",
            message="Доказательства инцидента временно недоступны",
            correlation_id=correlation_id,
        )

    status_value = str(incident.get("status") or "")
    if status_value not in {"open", "acknowledged", "executing", "resolved", "failed"}:
        return _problem(
            status_code=503,
            code="incident_status_invalid",
            message="Состояние инцидента не подтверждено",
            correlation_id=correlation_id,
        )

    timezone_name = settings.app_timezone
    timezone_known = True
    issues: list[OperatorIssue] = []
    if account_id:
        persisted_timezone = cabinet_days.timezone_names.get(account_id)
        if persisted_timezone:
            timezone_name = persisted_timezone
        else:
            timezone_name = "UTC"
            timezone_known = False
            issues.append(
                OperatorIssue(
                    code="cabinet_timezone_unknown",
                    title="Часовой пояс кабинета не подтверждён",
                    detail="Время показано в UTC; денежные действия остаются fail-closed.",
                    severity=OperatorSeverity.UNKNOWN,
                    correlation_id=None,
                )
            )

    currency_confirmed = currencies.state == "single"
    incident_item = _incident_item(incident, currency_state=currencies.state)
    if incident_item.requires_usd_evidence and not currency_confirmed:
        currency_issue = _currency_issue(currencies)
        if currency_issue is not None:
            issues.append(currency_issue)
    return OperatorIncidentDetailResponse(
        state=DataState.READY if not issues else DataState.PARTIAL,
        as_of=now,
        freshness_seconds=0,
        sources=["incidents", "meta_account_snapshot"],
        issues=issues,
        timezone=timezone_name,
        timezone_known=timezone_known,
        scope=_scope_evidence(
            cabinet_days=cabinet_days,
            currencies=currencies,
            display_timezone=settings.app_timezone,
        ),
        incident=incident_item,
    )


@router.post(
    "/incidents/{incident_id}/ack",
    response_model=OperatorIncidentAckResponse,
    responses=_PROBLEM_RESPONSES,
)
async def acknowledge_operator_incident(
    incident_id: str,
    engine: DepEngine,
    request: Request,
    acknowledged_by: str = Header(
        default="operator:web", alias="X-Operator-Principal", max_length=128
    ),
) -> OperatorIncidentAckResponse | JSONResponse:
    correlation_id = str(uuid.uuid4())
    try:
        internal_incident_id = parse_public_uuid(incident_id, prefix="inc")
    except ValueError:
        return _problem(
            status_code=404,
            code="incident_not_found",
            message="Инцидент не найден",
            correlation_id=correlation_id,
        )
    try:
        acknowledgement = await acknowledge_incident(
            engine,
            incident_id=internal_incident_id,
            acknowledged_by=getattr(request.state, "operator_principal", acknowledged_by),
        )
    except IncidentNotFoundError:
        return _problem(
            status_code=404,
            code="incident_not_found",
            message="Инцидент не найден",
            correlation_id=correlation_id,
        )
    except IncidentNotAcknowledgeableError as exc:
        return _problem(
            status_code=409,
            code="incident_not_acknowledgeable",
            message=f"Инцидент уже находится в состоянии {exc.status}",
            correlation_id=correlation_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "operator incident ack failed correlation_id=%s",
            correlation_id,
        )
        return _problem(
            status_code=503,
            code="incident_ack_unavailable",
            message="Подтверждение инцидента временно недоступно",
            correlation_id=correlation_id,
        )
    return OperatorIncidentAckResponse(
        incident_id=_public_incident_id(internal_incident_id),
        status="acknowledged",
        acknowledged_at=acknowledgement.acknowledged_at,
        correlation_id=_public_request_id(acknowledgement.correlation_id),
    )


@router.post(
    "/actions/{task_id}/manual-review",
    response_model=OperatorManualReviewResponse,
    responses=_PROBLEM_RESPONSES,
)
async def record_operator_manual_review(
    task_id: int,
    body: OperatorManualReviewRequest,
    engine: DepEngine,
    request: Request,
    reviewed_by: str = Header(default="operator:web", alias="X-Operator-Principal", max_length=128),
) -> OperatorManualReviewResponse | JSONResponse:
    """Зафиксировать, что оператор сверил задачу глазами, и что именно он увидел.

    Это НЕ команда в Meta и НЕ подтверждение исхода: ``state`` в ответе
    остаётся ``unknown``. Ответ 200 означает «факт записан», а не «внешняя
    операция удалась». Повтор с тем же наблюдением идемпотентен и возвращает
    ``recorded=false``.

    Личность берётся из доверенной границы аутентификации
    (``request.state.operator_principal``); заголовок — только запасное
    значение, как в остальных операторских командах. Роль проверяет middleware:
    POST относится к write-методам и для TMA доступен только владельцу.
    """
    correlation_id = str(uuid.uuid4())
    try:
        recorded = await record_manual_reconciliation(
            engine,
            task_id=task_id,
            observation=body.observation.value,
            reviewed_by=getattr(request.state, "operator_principal", reviewed_by),
        )
    except ManualReviewTaskNotFoundError:
        return _problem(
            status_code=404,
            code="action_not_found",
            message="Действие не найдено",
            correlation_id=correlation_id,
        )
    except ManualReviewNotApplicableError as exc:
        message = (
            "Задача ещё выполняется — дождитесь, пока система закончит сверку"
            if exc.reason == "task_is_still_running"
            else "У этой задачи исход уже определён, сверять нечего"
        )
        return _problem(
            status_code=409,
            code="manual_review_not_applicable",
            message=message,
            correlation_id=correlation_id,
        )
    except ValueError as exc:
        return _problem(
            status_code=422,
            code="invalid_manual_review",
            message=str(exc),
            correlation_id=correlation_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "operator manual review failed correlation_id=%s",
            correlation_id,
        )
        return _problem(
            status_code=503,
            code="manual_review_unavailable",
            message="Запись ручной сверки временно недоступна",
            correlation_id=correlation_id,
        )
    return OperatorManualReviewResponse(
        task_id=recorded.task_id,
        public_id=f"#{recorded.task_id}",
        # Ручная сверка не переписывает исход внешней операции.
        state=OperatorActionState.UNKNOWN,
        manual_review=OperatorActionManualReview(
            observation=OperatorManualReviewObservation(recorded.observation),
            at=recorded.reviewed_at,
            by=recorded.reviewed_by,
            question_closed=recorded.question_closed,
        ),
        recorded=recorded.was_changed,
        correlation_id=_public_request_id(recorded.correlation_id),
    )


__all__ = ["router"]
