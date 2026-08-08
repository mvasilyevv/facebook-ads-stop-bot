"""Read-only aggregation for the unified operator analytics page."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.analytics.budget import LiveBudget, calculate_live_budget
from core.dashboard.metric_aggregation import latest_per_ad_per_day_cte
from core.meta_api.account_tz import CabinetDayResolution

AnalyticsLevel = Literal["campaign", "adset", "ad"]
_MONEY = Decimal("0.01")
_PERCENT = Decimal("0.01")
_TRACKER_PROVIDER_AUDIT_KEY = "tracker_provider_reconciliation"
_TRACKER_FRESHNESS_SECONDS = 900
_TRACKER_FUTURE_TOLERANCE_SECONDS = 300


def _cabinet_boundary_case(
    *,
    campaign_alias: str,
    boundaries: dict[str, datetime] | None,
    prefix: str,
) -> tuple[str, dict[str, Any]]:
    """Build a bind-safe per-account start expression for live cabinet days."""
    if not boundaries:
        return "CAST(:from_dt AS timestamptz)", {}
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for index, (account_id, boundary) in enumerate(sorted(boundaries.items())):
        account_key = f"{prefix}_account_{index}"
        boundary_key = f"{prefix}_boundary_{index}"
        params[account_key] = account_id.removeprefix("act_")
        params[boundary_key] = boundary
        clauses.append(f"WHEN :{account_key} THEN CAST(:{boundary_key} AS timestamptz)")
    expression = (
        f"CASE {campaign_alias}.ad_account_id "
        + " ".join(clauses)
        + " ELSE CAST(:from_dt AS timestamptz) END"
    )
    return expression, params


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _decimal_string(value: Decimal, *, step: Decimal = _MONEY) -> str:
    return str(Decimal(value).quantize(step, rounding=ROUND_HALF_UP))


def _ratio(
    numerator: Decimal | int | None,
    denominator: Decimal | int | None,
    *,
    percent: bool,
) -> str | None:
    if numerator is None or denominator is None:
        return None
    den = _decimal(denominator)
    if den <= 0:
        return None
    value = _decimal(numerator) / den
    if percent:
        value *= Decimal("100")
        return _decimal_string(value, step=_PERCENT)
    return _decimal_string(value, step=Decimal("0.0001"))


def _metrics_payload(values: dict[str, Any]) -> dict[str, Any]:
    spend = _decimal_or_none(values.get("spend"))
    revenue = _decimal_or_none(values.get("revenue"))
    impressions = int(values["impressions"]) if values.get("impressions") is not None else None
    clicks = int(values["clicks"]) if values.get("clicks") is not None else None
    registrations = (
        int(values["registrations"]) if values.get("registrations") is not None else None
    )
    ftds = int(values["ftds"]) if values.get("ftds") is not None else None
    return {
        "spend": _decimal_string(spend) if spend is not None else None,
        "impressions": impressions,
        "clicks": clicks,
        "leads": int(values["leads"]) if values.get("leads") is not None else None,
        "registrations": registrations,
        "ftds": ftds,
        "confirmed_deposits": (
            int(values["confirmed_deposits"])
            if values.get("confirmed_deposits") is not None
            else None
        ),
        "redeposits": (int(values["redeposits"]) if values.get("redeposits") is not None else None),
        "revenue": _decimal_string(revenue) if revenue is not None else None,
        "cpc": _ratio(spend, clicks, percent=False),
        "ctr_pct": _ratio(clicks, impressions, percent=True),
        "click_registration_cr_pct": _ratio(registrations, clicks, percent=True),
        "registration_ftd_cr_pct": _ratio(ftds, registrations, percent=True),
        "cost_per_registration": _ratio(spend, registrations, percent=False),
        "cost_per_ftd": _ratio(spend, ftds, percent=False),
        "roi_pct": (
            _ratio(revenue - spend, spend, percent=True)
            if revenue is not None and spend is not None
            else None
        ),
        "roas": _ratio(revenue, spend, percent=False),
    }


def _budget_payload(budget: LiveBudget, *, mixed: bool = False) -> dict[str, Any]:
    return {
        "stage": "mixed" if mixed else budget.stage,
        "base_unit": None if mixed else _decimal_string(budget.base_unit),
        "stop_unit": None if mixed else _decimal_string(budget.stop_unit),
        "quantity": None if mixed else budget.quantity,
        "base_budget": _decimal_string(budget.base_budget),
        "stop_budget": _decimal_string(budget.stop_budget),
        "base_delta": _decimal_string(budget.base_delta),
        "stop_delta": _decimal_string(budget.stop_delta),
    }


def _sum_budgets(budgets: list[LiveBudget]) -> LiveBudget:
    first = budgets[0]
    return LiveBudget(
        stage=first.stage,
        base_unit=sum((b.base_unit for b in budgets), Decimal("0")),
        stop_unit=sum((b.stop_unit for b in budgets), Decimal("0")),
        quantity=sum(b.quantity for b in budgets),
        base_budget=sum((b.base_budget for b in budgets), Decimal("0")),
        stop_budget=sum((b.stop_budget for b in budgets), Decimal("0")),
        base_delta=sum((b.base_delta for b in budgets), Decimal("0")),
        stop_delta=sum((b.stop_delta for b in budgets), Decimal("0")),
    )


def _catalog_filters(
    *,
    level: AnalyticsLevel,
    parent_id: uuid.UUID | None,
    account_id: str | None,
    offer_id: uuid.UUID | None,
    campaign_id: uuid.UUID | None,
    search: str | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if level == "adset" and parent_id is not None:
        clauses.append("c.id = :parent_id")
        params["parent_id"] = parent_id
    if level == "ad" and parent_id is not None:
        clauses.append("s.id = :parent_id")
        params["parent_id"] = parent_id
    if account_id:
        clauses.append("c.ad_account_id = :account_id")
        params["account_id"] = account_id.removeprefix("act_")
    if offer_id is not None:
        clauses.append("c.offer_id = :offer_id")
        params["offer_id"] = offer_id
    if campaign_id is not None:
        clauses.append("c.id = :campaign_id")
        params["campaign_id"] = campaign_id
    if search:
        clauses.append(
            "(c.campaign_name ILIKE :search OR s.adset_name ILIKE :search "
            "OR a.ad_name ILIKE :search OR a.fb_ad_id ILIKE :search)"
        )
        params["search"] = f"%{search.strip()}%"
    return (" AND ".join(clauses) if clauses else "TRUE"), params


async def fetch_performance_rows(
    engine: AsyncEngine,
    *,
    from_dt: datetime,
    to_dt: datetime,
    is_live: bool,
    level: AnalyticsLevel,
    parent_id: uuid.UUID | None,
    account_id: str | None,
    offer_id: uuid.UUID | None,
    campaign_id: uuid.UUID | None,
    search: str | None,
    cabinet_boundaries: dict[str, datetime] | None = None,
    tracker_available: bool = False,
) -> list[dict[str, Any]]:
    """Load one lossless row per ad with Meta and exact-window Tracker metrics."""
    catalog_where, filter_params = _catalog_filters(
        level=level,
        parent_id=parent_id,
        account_id=account_id,
        offer_id=offer_id,
        campaign_id=campaign_id,
        search=search,
    )
    meta_boundary, meta_boundary_params = _cabinet_boundary_case(
        campaign_alias="mc",
        boundaries=cabinet_boundaries if is_live else None,
        prefix="meta",
    )
    tracker_boundary, tracker_boundary_params = _cabinet_boundary_case(
        campaign_alias="tc",
        boundaries=cabinet_boundaries if is_live else None,
        prefix="tracker",
    )
    event_boundary, event_boundary_params = _cabinet_boundary_case(
        campaign_alias="ec",
        boundaries=cabinet_boundaries if is_live else None,
        prefix="event",
    )
    scope_boundary, scope_boundary_params = _cabinet_boundary_case(
        campaign_alias="c",
        boundaries=cabinet_boundaries if is_live else None,
        prefix="scope",
    )
    meta_cte = (
        f"""meta_latest AS (
            SELECT DISTINCT ON (m.ad_id)
                m.ad_id, m.spend, m.impressions, m.clicks, m.leads,
                m.cycle_ts AS snapshot_at
            FROM ad_metrics m
            JOIN fb_ads ma ON ma.id = m.ad_id
            JOIN fb_adsets ms ON ms.id = ma.adset_id
            JOIN fb_campaigns mc ON mc.id = ms.campaign_id
            WHERE m.cycle_ts BETWEEN :from_dt AND :to_dt
              AND m.cycle_ts >= ({meta_boundary})
            ORDER BY m.ad_id, m.cycle_ts DESC
        )"""
        if is_live
        else latest_per_ad_per_day_cte(
            cte_alias="meta_latest",
            columns=("spend", "impressions", "clicks", "leads"),
            extra_select=", m.cycle_ts AS snapshot_at",
        )
    )
    sql = text(
        f"""
        WITH {meta_cte},
        meta_by_ad AS (
            SELECT ad_id,
                   CASE WHEN COUNT(*) = COUNT(spend) THEN SUM(spend) END AS spend,
                   CASE WHEN COUNT(*) = COUNT(impressions)
                        THEN SUM(impressions)::bigint END AS impressions,
                   CASE WHEN COUNT(*) = COUNT(clicks)
                        THEN SUM(clicks)::bigint END AS clicks,
                   CASE WHEN COUNT(*) = COUNT(leads)
                        THEN SUM(leads)::bigint END AS leads,
                   MAX(snapshot_at) AS meta_last_at
            FROM meta_latest
            GROUP BY ad_id
        ),
        tracker_state AS (
            SELECT ad_id,
                   COUNT(*) FILTER (
                       WHERE registration_at BETWEEN ({tracker_boundary}) AND :to_dt
                   )::bigint AS registrations,
                   COUNT(*) FILTER (
                       WHERE ftd_at BETWEEN ({tracker_boundary}) AND :to_dt
                   )::bigint AS ftds,
                   COUNT(*) FILTER (
                       WHERE confirmed_deposit_at BETWEEN ({tracker_boundary}) AND :to_dt
                   )::bigint AS confirmed_deposits,
                   MAX(last_event_at) FILTER (
                       WHERE last_event_at BETWEEN ({tracker_boundary}) AND :to_dt
                   ) AS tracker_last_at
            FROM tracker_click_state t
            JOIN fb_ads ta ON ta.id = t.ad_id
            JOIN fb_adsets tset ON tset.id = ta.adset_id
            JOIN fb_campaigns tc ON tc.id = tset.campaign_id
            WHERE t.ad_id IS NOT NULL
              AND (
                  registration_at BETWEEN ({tracker_boundary}) AND :to_dt
                  OR ftd_at BETWEEN ({tracker_boundary}) AND :to_dt
                  OR confirmed_deposit_at BETWEEN ({tracker_boundary}) AND :to_dt
                  OR last_event_at BETWEEN ({tracker_boundary}) AND :to_dt
              )
            GROUP BY t.ad_id
        ),
        tracker_events AS (
            SELECT e.fb_ad_fk AS ad_id,
                   COUNT(*) FILTER (
                       WHERE event_type = 'redeposit'
                   )::bigint AS redeposits,
                   CASE WHEN COUNT(*) FILTER (
                                WHERE event_type IN ('ftd', 'redeposit')
                            ) = COUNT(revenue) FILTER (
                                WHERE event_type IN ('ftd', 'redeposit')
                            )
                             AND COUNT(*) FILTER (
                                WHERE event_type IN ('ftd', 'redeposit')
                            ) = COUNT(currency) FILTER (
                                WHERE event_type IN ('ftd', 'redeposit')
                            )
                             AND COUNT(DISTINCT currency) FILTER (
                                WHERE event_type IN ('ftd', 'redeposit')
                            ) <= 1
                        THEN COALESCE(SUM(revenue) FILTER (
                            WHERE event_type IN ('ftd', 'redeposit')
                        ), 0)
                   END AS revenue,
                   MIN(currency) FILTER (
                       WHERE event_type IN ('ftd', 'redeposit')
                   ) AS tracker_currency,
                   (
                       COUNT(*) FILTER (
                           WHERE event_type IN ('ftd', 'redeposit')
                       ) = COUNT(currency) FILTER (
                           WHERE event_type IN ('ftd', 'redeposit')
                       )
                       AND COUNT(DISTINCT currency) FILTER (
                           WHERE event_type IN ('ftd', 'redeposit')
                       ) <= 1
                   ) AS tracker_currency_complete,
                   MAX(occurred_at) AS tracker_event_last_at
            FROM adsetpro_postback_events e
            JOIN fb_ads ea ON ea.id = e.fb_ad_fk
            JOIN fb_adsets eset ON eset.id = ea.adset_id
            JOIN fb_campaigns ec ON ec.id = eset.campaign_id
            WHERE occurred_at BETWEEN ({event_boundary}) AND :to_dt
              AND e.fb_ad_fk IS NOT NULL
              AND e.is_duplicate = false
              AND COALESCE(e.signature_valid, true) = true
              AND e.attribution_status LIKE 'matched%'
            GROUP BY e.fb_ad_fk
        )
        SELECT
            a.id AS ad_id, a.fb_ad_id, a.ad_name,
            s.id AS adset_id, s.fb_adset_id, s.adset_name,
            c.id AS campaign_id, c.fb_campaign_id, c.campaign_name, c.ad_account_id,
            cabinet_timezone.name AS cabinet_timezone,
            (cabinet_timezone.name IS NOT NULL) AS timezone_known,
            o.id AS offer_id, o.code AS offer_code,
            CASE
                WHEN account_snapshot.currency ~ '^[A-Z]{{3}}$'
                 AND r.currency = account_snapshot.currency
                    THEN r.cpa_threshold
            END AS cpa_threshold,
            r.stop_percent_of_rule,
            CASE
                WHEN account_snapshot.currency ~ '^[A-Z]{{3}}$'
                    THEN m.spend
            END AS spend,
            m.impressions::bigint AS impressions,
            m.clicks::bigint AS clicks,
            m.leads::bigint AS leads,
            CASE
                WHEN ts.ad_id IS NULL AND CAST(:tracker_available AS boolean) THEN 0
                ELSE ts.registrations
            END::bigint AS registrations,
            CASE
                WHEN ts.ad_id IS NULL AND CAST(:tracker_available AS boolean) THEN 0
                ELSE ts.ftds
            END::bigint AS ftds,
            CASE
                WHEN ts.ad_id IS NULL AND CAST(:tracker_available AS boolean) THEN 0
                ELSE ts.confirmed_deposits
            END::bigint AS confirmed_deposits,
            CASE
                WHEN te.ad_id IS NULL AND CAST(:tracker_available AS boolean) THEN 0
                ELSE te.redeposits
            END::bigint AS redeposits,
            CASE
                WHEN te.ad_id IS NULL AND CAST(:tracker_available AS boolean) THEN 0
                WHEN te.tracker_currency_complete
                 AND te.tracker_currency = account_snapshot.currency
                    THEN te.revenue
            END AS revenue,
            (
                account_snapshot.currency ~ '^[A-Z]{{3}}$'
            ) AS account_currency_complete,
            (
                r.cpa_threshold IS NULL
                OR (
                    r.currency ~ '^[A-Z]{{3}}$'
                    AND r.currency = account_snapshot.currency
                )
            ) AS rule_currency_complete,
            (
                te.ad_id IS NULL
                OR (
                    te.tracker_currency_complete
                    AND te.tracker_currency = account_snapshot.currency
                )
            ) AS tracker_currency_complete,
            (m.ad_id IS NOT NULL) AS meta_available,
            (
                ts.ad_id IS NOT NULL OR CAST(:tracker_available AS boolean)
            ) AS tracker_state_available,
            (
                te.ad_id IS NOT NULL OR CAST(:tracker_available AS boolean)
            ) AS tracker_events_available,
            m.meta_last_at,
            GREATEST(ts.tracker_last_at, te.tracker_event_last_at) AS tracker_last_at
        FROM fb_ads a
        JOIN fb_adsets s ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        LEFT JOIN offers o ON o.id = c.offer_id
        LEFT JOIN offer_rules r ON r.offer_id = o.id
        LEFT JOIN meta_account_snapshot account_snapshot
          ON account_snapshot.account_id = c.ad_account_id
        LEFT JOIN LATERAL (
            SELECT timezone_name.name
            FROM pg_catalog.pg_timezone_names timezone_name
            WHERE timezone_name.name = NULLIF(account_snapshot.timezone_name, '')
            LIMIT 1
        ) cabinet_timezone ON TRUE
        LEFT JOIN meta_by_ad m ON m.ad_id = a.id
        LEFT JOIN tracker_state ts ON ts.ad_id = a.id
        LEFT JOIN tracker_events te ON te.ad_id = a.id
        WHERE {catalog_where}
          AND a.first_seen_at < :to_dt
          AND (
              a.is_active = true
              OR a.last_seen_at >= ({scope_boundary})
          )
        ORDER BY c.campaign_name, s.adset_name, a.ad_name
        LIMIT 50001
        """
    )
    params = {
        "from_dt": from_dt,
        "to_dt": to_dt,
        "tracker_available": tracker_available,
        **filter_params,
        **meta_boundary_params,
        **tracker_boundary_params,
        **event_boundary_params,
        **scope_boundary_params,
    }
    async with engine.connect() as conn:
        return [dict(row) for row in (await conn.execute(sql, params)).mappings().all()]


def _identity(row: dict[str, Any], level: AnalyticsLevel) -> dict[str, Any]:
    if level == "campaign":
        return {
            "id": str(row["campaign_id"]),
            "fb_id": str(row["fb_campaign_id"]) if row.get("fb_campaign_id") else None,
            "name": str(row.get("campaign_name") or "—"),
            "parent_id": None,
            "parent_name": None,
            "child_id": str(row["adset_id"]),
        }
    if level == "adset":
        return {
            "id": str(row["adset_id"]),
            "fb_id": str(row["fb_adset_id"]) if row.get("fb_adset_id") else None,
            "name": str(row.get("adset_name") or "—"),
            "parent_id": str(row["campaign_id"]),
            "parent_name": str(row.get("campaign_name") or "—"),
            "child_id": str(row["ad_id"]),
        }
    return {
        "id": str(row["ad_id"]),
        "fb_id": str(row["fb_ad_id"]),
        "name": str(row.get("ad_name") or "—"),
        "parent_id": str(row["adset_id"]),
        "parent_name": str(row.get("adset_name") or "—"),
        "child_id": None,
    }


def aggregate_performance(
    raw_rows: list[dict[str, Any]],
    *,
    level: AnalyticsLevel,
    is_live: bool,
    sort: str,
    direction: Literal["asc", "desc"],
    page: int,
    page_size: int,
) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        identity = _identity(row, level)
        group = groups.setdefault(
            identity["id"],
            {
                **identity,
                "level": level,
                "children": set(),
                "ad_account_ids": set(),
                "cabinet_timezones": set(),
                "timezone_known": True,
                "offer_ids": set(),
                "offer_codes": set(),
                "spend": Decimal("0"),
                "impressions": 0,
                "clicks": 0,
                "leads": 0,
                "registrations": 0,
                "ftds": 0,
                "confirmed_deposits": 0,
                "redeposits": 0,
                "revenue": Decimal("0"),
                "metric_complete": {
                    "spend": True,
                    "impressions": True,
                    "clicks": True,
                    "leads": True,
                    "registrations": True,
                    "ftds": True,
                    "confirmed_deposits": True,
                    "redeposits": True,
                    "revenue": True,
                },
                "meta_complete": True,
                "tracker_state_complete": True,
                "tracker_events_complete": True,
                "account_currency_complete": True,
                "rule_currency_complete": True,
                "tracker_currency_complete": True,
                "has_evidence": False,
                "budgets": [],
                "unavailable_budgets": 0,
                "source_unavailable_budgets": 0,
            },
        )
        if identity["child_id"]:
            group["children"].add(identity["child_id"])
        if row.get("ad_account_id"):
            group["ad_account_ids"].add(str(row["ad_account_id"]))
        if row.get("cabinet_timezone"):
            group["cabinet_timezones"].add(str(row["cabinet_timezone"]))
        group["timezone_known"] = bool(group["timezone_known"] and row.get("timezone_known"))
        if row.get("offer_id"):
            group["offer_ids"].add(str(row["offer_id"]))
        if row.get("offer_code"):
            group["offer_codes"].add(str(row["offer_code"]))
        group["meta_complete"] = bool(group["meta_complete"] and row.get("meta_available"))
        group["tracker_state_complete"] = bool(
            group["tracker_state_complete"] and row.get("tracker_state_available")
        )
        group["tracker_events_complete"] = bool(
            group["tracker_events_complete"] and row.get("tracker_events_available")
        )
        group["account_currency_complete"] = bool(
            group["account_currency_complete"] and row.get("account_currency_complete")
        )
        group["rule_currency_complete"] = bool(
            group["rule_currency_complete"] and row.get("rule_currency_complete")
        )
        group["tracker_currency_complete"] = bool(
            group["tracker_currency_complete"] and row.get("tracker_currency_complete")
        )
        group["has_evidence"] = bool(
            group["has_evidence"]
            or row.get("meta_available")
            or row.get("tracker_state_available")
            or row.get("tracker_events_available")
        )
        for key in (
            "impressions",
            "clicks",
            "leads",
            "registrations",
            "ftds",
            "confirmed_deposits",
            "redeposits",
        ):
            if row.get(key) is None:
                group["metric_complete"][key] = False
            else:
                group[key] += int(row[key])
        for key in ("spend", "revenue"):
            value = _decimal_or_none(row.get(key))
            if value is None:
                group["metric_complete"][key] = False
            else:
                group[key] += value

        if is_live:
            required = (
                row.get("spend"),
                row.get("clicks"),
                row.get("leads"),
                row.get("registrations"),
                row.get("confirmed_deposits"),
            )
            if any(value is None for value in required):
                group["source_unavailable_budgets"] += 1
                group["unavailable_budgets"] += 1
            else:
                budget = calculate_live_budget(
                    actual_spend=_decimal(row["spend"]),
                    cpa_threshold=row.get("cpa_threshold"),
                    stop_percent_of_rule=row.get("stop_percent_of_rule"),
                    clicks=int(row["clicks"]),
                    leads=int(row["leads"]),
                    registrations=int(row["registrations"]),
                    confirmed_deposits=int(row["confirmed_deposits"]),
                )
                if budget is None:
                    group["unavailable_budgets"] += 1
                else:
                    group["budgets"].append(budget)

    rows: list[dict[str, Any]] = []
    for group in groups.values():
        metric_completeness = group.pop("metric_complete")
        for key, complete in metric_completeness.items():
            if not complete:
                group[key] = None
        metrics_complete = all(metric_completeness.values())
        metrics = _metrics_payload(group)
        budgets: list[LiveBudget] = group.pop("budgets")
        unavailable = int(group.pop("unavailable_budgets"))
        source_unavailable = int(group.pop("source_unavailable_budgets"))
        meta_complete = bool(group.pop("meta_complete"))
        tracker_state_complete = bool(group.pop("tracker_state_complete"))
        tracker_events_complete = bool(group.pop("tracker_events_complete"))
        account_currency_complete = bool(group.pop("account_currency_complete"))
        rule_currency_complete = bool(group.pop("rule_currency_complete"))
        tracker_currency_complete = bool(group.pop("tracker_currency_complete"))
        has_evidence = bool(group.pop("has_evidence"))
        issues: list[str] = []
        if not meta_complete:
            issues.append("Meta-метрики недоступны у части объектов")
        if not tracker_state_complete:
            issues.append("Регистрации и депозиты недоступны у части объектов")
        if not tracker_events_complete:
            issues.append("Revenue и повторные депозиты недоступны у части объектов")
        if not account_currency_complete:
            issues.append("Валюта Meta-кабинета не подтверждена")
        if not rule_currency_complete:
            issues.append("Валюта CPA правила не совпадает с валютой кабинета")
        if not tracker_currency_complete:
            issues.append("Валюта Tracker revenue не совпадает с валютой кабинета")
        if not metrics_complete:
            issues.append("Источник вернул не все поля метрик")
        state = (
            "unavailable"
            if not has_evidence
            else "ready"
            if (
                meta_complete
                and tracker_state_complete
                and tracker_events_complete
                and account_currency_complete
                and rule_currency_complete
                and tracker_currency_complete
                and metrics_complete
            )
            else "partial"
        )
        live_budget = None
        budget_reason = None
        if is_live and source_unavailable:
            budget_reason = "Нет подтверждённых Meta или Tracker-метрик для расчёта"
        elif is_live and unavailable:
            budget_reason = "Не задан CPA или оффер у части объявлений"
        elif is_live and budgets:
            combined = _sum_budgets(budgets)
            live_budget = _budget_payload(combined, mixed=len(budgets) > 1 or level != "ad")
        elif not is_live:
            budget_reason = "Budget delta доступен только за сегодня"

        rows.append(
            {
                "id": group["id"],
                "fb_id": group["fb_id"],
                "name": group["name"],
                "level": level,
                "parent_id": group["parent_id"],
                "parent_name": group["parent_name"],
                "has_children": bool(group["children"]),
                "ad_account_id": next(iter(group["ad_account_ids"]), None)
                if len(group["ad_account_ids"]) <= 1
                else None,
                "cabinet_timezone": (
                    next(iter(group["cabinet_timezones"]))
                    if group["timezone_known"] and len(group["cabinet_timezones"]) == 1
                    else None
                ),
                "timezone_known": bool(
                    group["timezone_known"] and len(group["cabinet_timezones"]) == 1
                ),
                "timezone_state": (
                    "unknown"
                    if not group["timezone_known"] or not group["cabinet_timezones"]
                    else "single"
                    if len(group["cabinet_timezones"]) == 1
                    else "mixed"
                ),
                "offer_id": next(iter(group["offer_ids"]), None)
                if len(group["offer_ids"]) <= 1
                else None,
                "offer_code": next(iter(group["offer_codes"]), None)
                if len(group["offer_codes"]) <= 1
                else "Несколько",
                "state": state,
                "issues": issues,
                **metrics,
                "live_budget": live_budget,
                "budget_unavailable_reason": budget_reason,
            }
        )

    sort_aliases = {
        "name": "name",
        "spend": "spend",
        "clicks": "clicks",
        "registrations": "registrations",
        "ftds": "ftds",
        "confirmed_deposits": "confirmed_deposits",
        "revenue": "revenue",
        "base_delta": "base_delta",
    }
    sort_key = sort_aliases.get(sort, "spend")

    def row_sort_value(row: dict[str, Any]) -> Any | None:
        if sort_key == "base_delta":
            budget = row.get("live_budget") or {}
            return _decimal_or_none(budget.get("base_delta"))
        if sort_key in {"spend", "revenue"}:
            return _decimal_or_none(row.get(sort_key))
        return row.get(sort_key)

    rows.sort(key=lambda row: row["id"])
    known_rows = [row for row in rows if row_sort_value(row) is not None]
    unknown_rows = [row for row in rows if row_sort_value(row) is None]
    known_rows.sort(key=row_sort_value, reverse=direction == "desc")
    rows = known_rows + unknown_rows
    total = len(rows)
    pages = math.ceil(total / page_size) if total else 0
    resolved_page = min(max(page, 1), pages) if pages else 1
    start = (resolved_page - 1) * page_size
    paged = rows[start : start + page_size]

    totals_acc: dict[str, Any] = {}
    for key in ("spend", "revenue"):
        values = [_decimal_or_none(row.get(key)) for row in rows]
        totals_acc[key] = (
            sum((value for value in values if value is not None), Decimal("0"))
            if values and all(value is not None for value in values)
            else None
        )
    for key in (
        "impressions",
        "clicks",
        "leads",
        "registrations",
        "ftds",
        "confirmed_deposits",
        "redeposits",
    ):
        values = [row.get(key) for row in rows]
        totals_acc[key] = (
            sum(int(value) for value in values if value is not None)
            if values and all(value is not None for value in values)
            else None
        )

    total_budget = None
    total_budget_reason = None
    if is_live:
        if rows and all(r.get("live_budget") is not None for r in rows):
            total_budget = {
                "stage": "mixed",
                "base_unit": None,
                "stop_unit": None,
                "quantity": None,
                "base_budget": _decimal_string(
                    sum((_decimal(r["live_budget"]["base_budget"]) for r in rows), Decimal("0"))
                ),
                "stop_budget": _decimal_string(
                    sum((_decimal(r["live_budget"]["stop_budget"]) for r in rows), Decimal("0"))
                ),
                "base_delta": _decimal_string(
                    sum((_decimal(r["live_budget"]["base_delta"]) for r in rows), Decimal("0"))
                ),
                "stop_delta": _decimal_string(
                    sum((_decimal(r["live_budget"]["stop_delta"]) for r in rows), Decimal("0"))
                ),
            }
        elif rows:
            total_budget_reason = "Не для всех объявлений доступны оффер и CPA"
    else:
        total_budget_reason = "Budget delta доступен только за сегодня"

    return {
        "rows": paged,
        "totals": _metrics_payload(totals_acc),
        "total_live_budget": total_budget,
        "total_budget_unavailable_reason": total_budget_reason,
        "pagination": {
            "page": resolved_page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
        },
        "_quality": {
            "has_rows": bool(rows),
            "has_evidence": any(row["state"] != "unavailable" for row in rows),
            "has_partial_rows": any(row["state"] == "partial" for row in rows),
        },
    }


async def fetch_source_quality(
    engine: AsyncEngine,
    *,
    from_dt: datetime,
    to_dt: datetime,
    cabinet_days: CabinetDayResolution | None = None,
    account_id: str | None = None,
    offer_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
) -> dict[str, dict[str, Any]]:
    catalog_where, filter_params = _catalog_filters(
        level="campaign",
        parent_id=None,
        account_id=account_id,
        offer_id=offer_id,
        campaign_id=campaign_id,
        search=None,
    )
    sql = text(
        f"""
        WITH scoped_ads AS (
            SELECT a.id, a.fb_ad_id
            FROM fb_ads a
            JOIN fb_adsets s ON s.id = a.adset_id
            JOIN fb_campaigns c ON c.id = s.campaign_id
            LEFT JOIN offers o ON o.id = c.offer_id
            WHERE {catalog_where}
              AND a.first_seen_at < :to_dt
              AND (
                  a.is_active = true
                  OR a.last_seen_at >= :from_dt
              )
        ), tracker_audit AS (
            SELECT
                COUNT(*)::bigint AS provider_audit_rows,
                MAX(value->>'status') FILTER (
                    WHERE key = :tracker_provider_audit_key
                ) AS provider_status,
                MAX(value->>'checked_at') FILTER (
                    WHERE key = :tracker_provider_audit_key
                ) AS provider_checked_at,
                MAX(value->>'window_start') FILTER (
                    WHERE key = :tracker_provider_audit_key
                ) AS provider_window_start,
                MAX(value->>'window_end') FILTER (
                    WHERE key = :tracker_provider_audit_key
                ) AS provider_window_end,
                MAX(value->>'drift_after') FILTER (
                    WHERE key = :tracker_provider_audit_key
                ) AS provider_drift_after,
                MAX(value->>'skipped') FILTER (
                    WHERE key = :tracker_provider_audit_key
                ) AS provider_skipped
            FROM system_config
            WHERE key = :tracker_provider_audit_key
        )
        SELECT
          (SELECT MAX(m.cycle_ts)
             FROM ad_metrics m
             JOIN scoped_ads scope ON scope.id = m.ad_id
            WHERE m.cycle_ts BETWEEN :from_dt AND :to_dt) AS meta_last_at,
          tracker_audit.provider_audit_rows,
          tracker_audit.provider_status,
          tracker_audit.provider_checked_at,
          tracker_audit.provider_window_start,
          tracker_audit.provider_window_end,
          tracker_audit.provider_drift_after,
          tracker_audit.provider_skipped,
          (SELECT COUNT(*)
             FROM adsetpro_postback_events e
             JOIN scoped_ads scope ON scope.id = e.fb_ad_fk
                OR (e.fb_ad_fk IS NULL AND scope.fb_ad_id = e.fb_ad_id)
            WHERE e.occurred_at BETWEEN :from_dt AND :to_dt
              AND e.is_duplicate = false
              AND COALESCE(e.signature_valid, true) = true
              AND e.attribution_status NOT LIKE 'matched%')::bigint AS unmatched_events,
          (SELECT COUNT(*)
             FROM adsetpro_postback_events e
             JOIN scoped_ads scope ON scope.id = e.fb_ad_fk
                OR (e.fb_ad_fk IS NULL AND scope.fb_ad_id = e.fb_ad_id)
            WHERE e.occurred_at BETWEEN :from_dt AND :to_dt
              AND e.is_duplicate = false
              AND COALESCE(e.signature_valid, true) = true)::bigint AS tracker_events,
          (SELECT COUNT(*)
             FROM task_queue q
             JOIN adsetpro_postback_events e
               ON e.id = CASE
                   WHEN q.payload->>'event_id' ~ '^[0-9]+$'
                   THEN (q.payload->>'event_id')::bigint
               END
             JOIN scoped_ads scope ON scope.id = e.fb_ad_fk
                OR (e.fb_ad_fk IS NULL AND scope.fb_ad_id = e.fb_ad_id)
            WHERE q.task_type = 'tracker_event_process'
              AND q.status IN ('pending', 'retrying', 'running')
              AND e.occurred_at BETWEEN :from_dt AND :to_dt)::bigint
              AS pending_tracker_tasks
        FROM tracker_audit
        """
    )
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    sql,
                    {
                        "from_dt": from_dt,
                        "to_dt": to_dt,
                        "tracker_provider_audit_key": _TRACKER_PROVIDER_AUDIT_KEY,
                        **filter_params,
                    },
                )
            )
            .mappings()
            .one()
        )

    now = datetime.now(UTC)
    freshness_reference = min(now, to_dt)

    def lag(ts: datetime | None) -> int | None:
        if ts is None:
            return None
        return max(0, int((freshness_reference - ts).total_seconds()))

    meta_last = row["meta_last_at"]

    def parse_timestamp(value: Any) -> tuple[datetime | None, bool]:
        if not value:
            return None, False
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            else:
                parsed = parsed.astimezone(UTC)
        except (TypeError, ValueError):
            return None, True
        return parsed, False

    provider_checked_at, provider_checked_invalid = parse_timestamp(row["provider_checked_at"])
    provider_window_start, provider_start_invalid = parse_timestamp(row["provider_window_start"])
    provider_window_end, provider_end_invalid = parse_timestamp(row["provider_window_end"])
    provider_status_raw = row["provider_status"]
    provider_audit_present = int(row["provider_audit_rows"] or 0) > 0
    provider_numbers_invalid = False
    if not provider_audit_present:
        provider_drift_after = 0
        provider_skipped = 0
    else:
        try:
            provider_drift_after = int(row["provider_drift_after"])
            provider_skipped = int(row["provider_skipped"])
        except (TypeError, ValueError):
            provider_drift_after = 0
            provider_skipped = 0
            provider_numbers_invalid = True
    tracker_last = provider_checked_at
    unmatched = int(row["unmatched_events"] or 0)
    tracker_events = int(row["tracker_events"] or 0)
    pending_tracker_tasks = int(row["pending_tracker_tasks"] or 0)
    meta_status = (
        "missing" if meta_last is None else ("good" if (lag(meta_last) or 0) <= 900 else "degraded")
    )
    meta_issues: list[str] = []
    if meta_last is None:
        meta_issues.append("В выбранном окне нет подтверждённых Meta-снимков")
    elif (lag(meta_last) or 0) > 900:
        meta_issues.append(f"Последний Meta-снимок был {lag(meta_last)} с назад")
    missing_timezone_accounts: list[str] = []
    timezone_known: bool | None = None
    if cabinet_days is not None:
        timezone_known = cabinet_days.timezone_known
        missing_timezone_accounts = list(cabinet_days.missing_account_ids)
        if not cabinet_days.account_ids:
            meta_issues.append("Не найден активный кабинет для определения часового пояса")
        elif missing_timezone_accounts:
            meta_issues.append(
                "Часовой пояс кабинета неизвестен; границы суток и суммы являются оценочными"
            )
        if meta_issues and meta_status == "good":
            meta_status = "degraded"
    tracker_lag = lag(tracker_last)
    future_limit = now + timedelta(seconds=_TRACKER_FUTURE_TOLERANCE_SECONDS)
    watermark_from_future = any(
        value is not None and value > future_limit
        for value in (
            provider_checked_at,
            provider_window_start,
            provider_window_end,
        )
    )
    audit_invalid = any(
        (
            provider_checked_invalid,
            provider_start_invalid,
            provider_end_invalid,
            provider_numbers_invalid,
        )
    )
    provider_lag = lag(provider_checked_at)
    provider_covers_window = bool(
        provider_window_start
        and provider_window_end
        and provider_window_start <= from_dt
        and provider_window_end
        >= freshness_reference - timedelta(seconds=_TRACKER_FRESHNESS_SECONDS)
    )
    if audit_invalid:
        tracker_status = "unknown"
        tracker_note = "Некорректный durable audit Tracker pipeline"
    elif (
        provider_checked_at is None
        or provider_window_start is None
        or provider_window_end is None
        or provider_status_raw is None
    ):
        tracker_status = "unknown"
        tracker_note = "Нет durable provider audit; нулевые конверсии не подтверждены"
    elif watermark_from_future:
        tracker_status = "degraded"
        tracker_note = "Durable audit Tracker pipeline содержит timestamp из будущего"
    elif provider_lag is not None and provider_lag > _TRACKER_FRESHNESS_SECONDS:
        tracker_status = "degraded"
        tracker_note = f"Последний подтверждённый цикл Tracker был {tracker_lag} с назад"
    elif str(provider_status_raw) != "ok":
        tracker_status = "degraded"
        tracker_note = f"Provider reconciliation завершён со статусом {provider_status_raw}"
    elif provider_drift_after > 0 or provider_skipped > 0:
        tracker_status = "degraded"
        tracker_note = (
            "Provider reconciliation не подтвердил полное покрытие: "
            f"drift={provider_drift_after}, skipped={provider_skipped}"
        )
    elif not provider_covers_window:
        tracker_status = "degraded"
        tracker_note = "Provider reconciliation не покрывает всё выбранное окно"
    elif pending_tracker_tasks > 0:
        tracker_status = "degraded"
        tracker_note = f"Ожидают применения Tracker-события: {pending_tracker_tasks}"
    elif unmatched > 0:
        tracker_status = "degraded"
        tracker_note = f"Не атрибутировано событий: {unmatched}"
    else:
        tracker_status = "good"
        tracker_note = (
            "Tracker inbox и provider reconciliation подтверждены; отсутствие событий означает ноль"
            if tracker_events == 0
            else "AdSet.pro — источник регистраций и депозитов"
        )
    return {
        "meta": {
            "source": "meta",
            "status": meta_status,
            "last_event_at": meta_last,
            "lag_seconds": lag(meta_last),
            "timezone_known": timezone_known,
            "missing_timezone_account_ids": missing_timezone_accounts,
            "issues": meta_issues,
            "note": (meta_issues[0] if meta_issues else "Spend, impressions и clicks из Meta"),
        },
        "tracker": {
            "source": "tracker",
            "status": tracker_status,
            "last_event_at": tracker_last,
            "lag_seconds": tracker_lag,
            "unmatched_events": unmatched,
            "issues": [] if tracker_status == "good" else [tracker_note],
            "note": tracker_note,
        },
    }


async def fetch_filter_options(engine: AsyncEngine) -> dict[str, list[dict[str, str]]]:
    sql = text(
        """
        SELECT c.id AS campaign_id, c.fb_campaign_id, c.campaign_name, c.ad_account_id,
               o.id AS offer_id, o.code AS offer_code, o.name AS offer_name
        FROM fb_campaigns c
        LEFT JOIN offers o ON o.id = c.offer_id
        WHERE c.last_seen_at >= NOW() - INTERVAL '90 days'
        ORDER BY c.last_seen_at DESC
        LIMIT 1000
        """
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(sql)).mappings().all()
    accounts: dict[str, str] = {}
    offers: dict[str, str] = {}
    campaigns: dict[str, str] = {}
    for row in rows:
        if row["ad_account_id"]:
            value = str(row["ad_account_id"])
            accounts[value] = f"act_{value}"
        if row["offer_id"]:
            offers[str(row["offer_id"])] = str(row["offer_code"] or row["offer_name"] or "—")
        campaigns[str(row["campaign_id"])] = str(
            row["campaign_name"] or row["fb_campaign_id"] or "—"
        )
    return {
        "accounts": [
            {"value": k, "label": v} for k, v in sorted(accounts.items(), key=lambda item: item[1])
        ],
        "offers": [
            {"value": k, "label": v} for k, v in sorted(offers.items(), key=lambda item: item[1])
        ],
        "campaigns": [{"value": k, "label": v} for k, v in campaigns.items()],
    }


async def fetch_live_budget_points(
    engine: AsyncEngine,
    *,
    from_dt: datetime,
    to_dt: datetime,
    account_id: str | None,
    offer_id: uuid.UUID | None,
    campaign_id: uuid.UUID | None,
    cabinet_boundaries: dict[str, datetime] | None = None,
) -> list[dict[str, Any]]:
    catalog_where, params = _catalog_filters(
        level="campaign",
        parent_id=None,
        account_id=account_id,
        offer_id=offer_id,
        campaign_id=campaign_id,
        search=None,
    )
    metric_boundary, metric_boundary_params = _cabinet_boundary_case(
        campaign_alias="c",
        boundaries=cabinet_boundaries,
        prefix="series_metric",
    )
    event_boundary, event_boundary_params = _cabinet_boundary_case(
        campaign_alias="c",
        boundaries=cabinet_boundaries,
        prefix="series_event",
    )
    sql = text(
        f"""
        SELECT DISTINCT ON (m.ad_id, date_trunc('hour', m.cycle_ts))
            m.ad_id,
            date_trunc('hour', m.cycle_ts) AS bucket_ts,
            m.cycle_ts,
            CASE
                WHEN account_snapshot.currency ~ '^[A-Z]{{3}}$'
                    THEN m.spend
            END AS spend,
            m.clicks, m.leads,
            CASE
                WHEN account_snapshot.currency ~ '^[A-Z]{{3}}$'
                 AND r.currency = account_snapshot.currency
                    THEN r.cpa_threshold
            END AS cpa_threshold,
            r.stop_percent_of_rule
        FROM ad_metrics m
        JOIN fb_ads a ON a.id = m.ad_id
        JOIN fb_adsets s ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        LEFT JOIN offers o ON o.id = c.offer_id
        LEFT JOIN offer_rules r ON r.offer_id = o.id
        LEFT JOIN meta_account_snapshot account_snapshot
          ON account_snapshot.account_id = c.ad_account_id
        WHERE m.cycle_ts BETWEEN :from_dt AND :to_dt
          AND m.cycle_ts >= ({metric_boundary})
          AND {catalog_where}
        ORDER BY m.ad_id, date_trunc('hour', m.cycle_ts), m.cycle_ts DESC
        """
    )
    expected_sql = text(
        f"""
        SELECT
            a.id AS ad_id,
            a.is_active,
            a.first_seen_at,
            a.last_seen_at
        FROM fb_ads a
        JOIN fb_adsets s ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        LEFT JOIN offers o ON o.id = c.offer_id
        WHERE {catalog_where}
          AND a.first_seen_at < :to_dt
          AND (
              a.is_active = true
              OR a.last_seen_at >= ({metric_boundary})
          )
        ORDER BY a.id
        """
    )
    event_sql = text(
        f"""
        SELECT t.ad_id, t.registration_at, t.confirmed_deposit_at
        FROM tracker_click_state t
        JOIN fb_ads a ON a.id = t.ad_id
        JOIN fb_adsets s ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        LEFT JOIN offers o ON o.id = c.offer_id
        WHERE t.ad_id IS NOT NULL
          AND (
            t.registration_at BETWEEN ({event_boundary}) AND :to_dt
            OR t.confirmed_deposit_at BETWEEN ({event_boundary}) AND :to_dt
          )
          AND {catalog_where}
        """
    )
    bind = {
        "from_dt": from_dt,
        "to_dt": to_dt,
        **params,
        **metric_boundary_params,
        **event_boundary_params,
    }
    async with engine.connect() as conn:
        expected_ads = [
            dict(row) for row in (await conn.execute(expected_sql, bind)).mappings().all()
        ]
        snapshots = [dict(r) for r in (await conn.execute(sql, bind)).mappings().all()]
        events = [dict(r) for r in (await conn.execute(event_sql, bind)).mappings().all()]

    by_hour: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in snapshots:
        by_hour[row["bucket_ts"]].append(row)
    event_by_ad: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        event_by_ad[row["ad_id"]].append(row)

    start_hour = from_dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    end_hour = to_dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    points: list[dict[str, Any]] = []
    cursor = start_hour
    while cursor <= end_hour:
        point_end = min(cursor + timedelta(hours=1), to_dt)
        expected_ad_ids = {
            row["ad_id"]
            for row in expected_ads
            if row["first_seen_at"] < point_end
            and (bool(row["is_active"]) or row["last_seen_at"] >= cursor)
        }
        current_by_ad = {
            row["ad_id"]: row for row in by_hour.get(cursor, []) if row["ad_id"] in expected_ad_ids
        }
        budgets: list[LiveBudget] = []
        unavailable = len(expected_ad_ids.difference(current_by_ad))
        spend_values: list[Decimal | None] = []
        for row in current_by_ad.values():
            ad_id = row["ad_id"]
            spend_values.append(_decimal_or_none(row.get("spend")))
            registrations = sum(
                1
                for event in event_by_ad.get(ad_id, [])
                if event.get("registration_at") is not None
                and from_dt <= event["registration_at"] <= point_end
            )
            confirmed = sum(
                1
                for event in event_by_ad.get(ad_id, [])
                if event.get("confirmed_deposit_at") is not None
                and from_dt <= event["confirmed_deposit_at"] <= point_end
            )
            if row.get("spend") is None or row.get("clicks") is None or row.get("leads") is None:
                budget = None
            else:
                budget = calculate_live_budget(
                    actual_spend=_decimal(row["spend"]),
                    cpa_threshold=row.get("cpa_threshold"),
                    stop_percent_of_rule=row.get("stop_percent_of_rule"),
                    clicks=int(row["clicks"]),
                    leads=int(row["leads"]),
                    registrations=registrations,
                    confirmed_deposits=confirmed,
                )
            if budget is None:
                unavailable += 1
            else:
                budgets.append(budget)
        if expected_ad_ids:
            actual = (
                sum(
                    (value for value in spend_values if value is not None),
                    Decimal("0"),
                )
                if len(current_by_ad) == len(expected_ad_ids)
                and all(value is not None for value in spend_values)
                else None
            )
            budget_complete = len(budgets) == len(expected_ad_ids) and unavailable == 0
            points.append(
                {
                    "ts": cursor,
                    "actual": _decimal_string(actual) if actual is not None else None,
                    "base": (
                        _decimal_string(sum((b.base_budget for b in budgets), Decimal("0")))
                        if budget_complete
                        else None
                    ),
                    "stop": (
                        _decimal_string(sum((b.stop_budget for b in budgets), Decimal("0")))
                        if budget_complete
                        else None
                    ),
                    "available_ads": len(budgets),
                    "unavailable_ads": unavailable,
                }
            )
        cursor += timedelta(hours=1)
    return points


async def fetch_daypart_cells(
    engine: AsyncEngine,
    *,
    from_dt: datetime,
    to_dt: datetime,
    timezone_name: str,
    account_id: str | None,
    offer_id: uuid.UUID | None,
    campaign_id: uuid.UUID | None,
    tracker_available: bool = False,
) -> list[dict[str, int | None]]:
    catalog_where, params = _catalog_filters(
        level="campaign",
        parent_id=None,
        account_id=account_id,
        offer_id=offer_id,
        campaign_id=campaign_id,
        search=None,
    )
    clicks_sql = text(
        f"""
        WITH scoped_ads AS (
            SELECT
                a.id,
                a.is_active,
                a.first_seen_at,
                a.last_seen_at,
                COALESCE(cabinet_timezone.name, 'UTC') AS cabinet_timezone
            FROM fb_ads a
            JOIN fb_adsets s ON s.id = a.adset_id
            JOIN fb_campaigns c ON c.id = s.campaign_id
            LEFT JOIN offers o ON o.id = c.offer_id
            LEFT JOIN meta_account_snapshot account_snapshot
              ON account_snapshot.account_id = c.ad_account_id
            LEFT JOIN LATERAL (
                SELECT timezone_name.name
                FROM pg_catalog.pg_timezone_names timezone_name
                WHERE timezone_name.name = NULLIF(account_snapshot.timezone_name, '')
                LIMIT 1
            ) cabinet_timezone ON TRUE
            WHERE {catalog_where}
              AND a.first_seen_at < :to_dt
              AND (
                  a.is_active = true
                  OR a.last_seen_at >= :from_dt
              )
        ),
        hourly AS (
            SELECT DISTINCT ON (m.ad_id, date_trunc('hour', m.cycle_ts))
                m.ad_id, date_trunc('hour', m.cycle_ts) AS bucket_ts,
                m.clicks::bigint AS clicks,
                timezone(
                    scope.cabinet_timezone,
                    date_trunc('hour', m.cycle_ts)
                )::date AS cabinet_day
            FROM ad_metrics m
            JOIN scoped_ads scope ON scope.id = m.ad_id
            WHERE m.cycle_ts BETWEEN :baseline_from AND :to_dt
              AND scope.first_seen_at < LEAST(
                  date_trunc('hour', m.cycle_ts) + INTERVAL '1 hour',
                  :to_dt
              )
              AND (
                  scope.is_active = true
                  OR scope.last_seen_at >= date_trunc('hour', m.cycle_ts)
              )
            ORDER BY m.ad_id, date_trunc('hour', m.cycle_ts), m.cycle_ts DESC
        ), with_previous AS (
            SELECT ad_id, bucket_ts, clicks, cabinet_day,
                   LAG(bucket_ts) OVER (
                       PARTITION BY ad_id ORDER BY bucket_ts
                   ) AS previous_bucket_ts,
                   LAG(clicks) OVER (
                       PARTITION BY ad_id ORDER BY bucket_ts
                   ) AS previous_clicks,
                   LAG(cabinet_day) OVER (
                       PARTITION BY ad_id ORDER BY bucket_ts
                   ) AS previous_cabinet_day
            FROM hourly
        ), deltas AS (
            SELECT ad_id, bucket_ts,
                   CASE
                       WHEN clicks IS NULL THEN NULL
                       WHEN previous_bucket_ts IS NULL THEN NULL
                       WHEN bucket_ts <> previous_bucket_ts + INTERVAL '1 hour' THEN NULL
                       WHEN cabinet_day <> previous_cabinet_day THEN clicks
                       WHEN previous_clicks IS NULL THEN NULL
                       WHEN clicks >= previous_clicks THEN clicks - previous_clicks
                       ELSE NULL
                   END AS click_delta
            FROM with_previous
        ), range_bounds AS (
            SELECT
                date_trunc('hour', CAST(:from_dt AS timestamptz)) AS start_bucket,
                date_trunc('hour', CAST(:to_dt AS timestamptz)) AS end_bucket
        ), scope_intervals AS (
            SELECT
                scope.id,
                GREATEST(
                    date_trunc('hour', scope.first_seen_at),
                    bounds.start_bucket
                ) AS start_bucket,
                CASE
                    WHEN scope.is_active = true THEN bounds.end_bucket
                    ELSE LEAST(
                        date_trunc('hour', scope.last_seen_at),
                        bounds.end_bucket
                    )
                END AS end_bucket
            FROM scoped_ads scope
            CROSS JOIN range_bounds bounds
        ), coverage_changes AS (
            SELECT start_bucket AS bucket_ts, 1::bigint AS delta
            FROM scope_intervals
            WHERE start_bucket <= end_bucket
            UNION ALL
            SELECT end_bucket + INTERVAL '1 hour' AS bucket_ts, (-1)::bigint AS delta
            FROM scope_intervals
            WHERE start_bucket <= end_bucket
        ), change_by_hour AS (
            SELECT bucket_ts, SUM(delta)::bigint AS delta
            FROM coverage_changes
            GROUP BY bucket_ts
        ), coverage AS (
            SELECT
                series.bucket_ts,
                SUM(COALESCE(changes.delta, 0)) OVER (
                    ORDER BY series.bucket_ts
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                )::bigint AS expected_ads
            FROM range_bounds bounds
            CROSS JOIN LATERAL generate_series(
                bounds.start_bucket,
                bounds.end_bucket,
                INTERVAL '1 hour'
            ) AS series(bucket_ts)
            LEFT JOIN change_by_hour changes
              ON changes.bucket_ts = series.bucket_ts
        ), hourly_values AS (
            SELECT
                coverage.bucket_ts,
                CASE
                    WHEN COUNT(deltas.ad_id) = coverage.expected_ads
                     AND COUNT(deltas.click_delta) = coverage.expected_ads
                    THEN SUM(deltas.click_delta)::bigint
                END AS clicks
            FROM coverage
            LEFT JOIN deltas ON deltas.bucket_ts = coverage.bucket_ts
            WHERE coverage.expected_ads > 0
            GROUP BY coverage.bucket_ts, coverage.expected_ads
        )
        SELECT
            EXTRACT(ISODOW FROM timezone(:timezone_name, bucket_ts))::int AS weekday,
            EXTRACT(HOUR FROM timezone(:timezone_name, bucket_ts))::int AS hour,
            CASE WHEN COUNT(*) = COUNT(clicks)
                 THEN SUM(clicks)::bigint END AS clicks
        FROM hourly_values
        GROUP BY weekday, hour
        """
    )
    tracker_sql = text(
        f"""
        SELECT
            EXTRACT(ISODOW FROM timezone(:timezone_name, event_ts))::int AS weekday,
            EXTRACT(HOUR FROM timezone(:timezone_name, event_ts))::int AS hour,
            SUM(registrations)::bigint AS registrations,
            SUM(ftds)::bigint AS ftds
        FROM (
            SELECT t.registration_at AS event_ts, 1 AS registrations, 0 AS ftds
            FROM tracker_click_state t
            JOIN fb_ads a ON a.id = t.ad_id
            JOIN fb_adsets s ON s.id = a.adset_id
            JOIN fb_campaigns c ON c.id = s.campaign_id
            LEFT JOIN offers o ON o.id = c.offer_id
            WHERE t.registration_at BETWEEN :from_dt AND :to_dt
              AND {catalog_where}
              AND a.first_seen_at < :to_dt
              AND (a.is_active = true OR a.last_seen_at >= :from_dt)
            UNION ALL
            SELECT t.ftd_at AS event_ts, 0 AS registrations, 1 AS ftds
            FROM tracker_click_state t
            JOIN fb_ads a ON a.id = t.ad_id
            JOIN fb_adsets s ON s.id = a.adset_id
            JOIN fb_campaigns c ON c.id = s.campaign_id
            LEFT JOIN offers o ON o.id = c.offer_id
            WHERE t.ftd_at BETWEEN :from_dt AND :to_dt
              AND {catalog_where}
              AND a.first_seen_at < :to_dt
              AND (a.is_active = true OR a.last_seen_at >= :from_dt)
        ) events
        GROUP BY weekday, hour
        """
    )
    bind = {
        "from_dt": from_dt,
        "baseline_from": from_dt - timedelta(days=1),
        "to_dt": to_dt,
        "timezone_name": timezone_name,
        **params,
    }
    async with engine.connect() as conn:
        click_rows = (await conn.execute(clicks_sql, bind)).mappings().all()
        tracker_rows = (await conn.execute(tracker_sql, bind)).mappings().all()
    cells: dict[tuple[int, int], dict[str, int | None]] = {}
    for row in click_rows:
        key = (int(row["weekday"]), int(row["hour"]))
        cells[key] = {
            "weekday": key[0],
            "hour": key[1],
            "clicks": int(row["clicks"]) if row["clicks"] is not None else None,
            "registrations": 0 if tracker_available else None,
            "ftds": 0 if tracker_available else None,
        }
    for row in tracker_rows:
        key = (int(row["weekday"]), int(row["hour"]))
        cell = cells.setdefault(
            key,
            {
                "weekday": key[0],
                "hour": key[1],
                "clicks": None,
                "registrations": None,
                "ftds": None,
            },
        )
        cell["registrations"] = (
            int(row["registrations"]) if row["registrations"] is not None else None
        )
        cell["ftds"] = int(row["ftds"]) if row["ftds"] is not None else None
    return [cells[key] for key in sorted(cells)]


__all__ = [
    "aggregate_performance",
    "fetch_daypart_cells",
    "fetch_filter_options",
    "fetch_live_budget_points",
    "fetch_performance_rows",
    "fetch_source_quality",
]
