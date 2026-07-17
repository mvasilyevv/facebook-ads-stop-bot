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

AnalyticsLevel = Literal["campaign", "adset", "ad"]
_MONEY = Decimal("0.01")
_PERCENT = Decimal("0.01")


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


def _decimal_string(value: Decimal, *, step: Decimal = _MONEY) -> str:
    return str(Decimal(value).quantize(step, rounding=ROUND_HALF_UP))


def _ratio(numerator: Decimal | int, denominator: Decimal | int, *, percent: bool) -> str | None:
    den = _decimal(denominator)
    if den <= 0:
        return None
    value = _decimal(numerator) / den
    if percent:
        value *= Decimal("100")
        return _decimal_string(value, step=_PERCENT)
    return _decimal_string(value, step=Decimal("0.0001"))


def _metrics_payload(values: dict[str, Any]) -> dict[str, Any]:
    spend = _decimal(values.get("spend"))
    revenue = _decimal(values.get("revenue"))
    impressions = int(values.get("impressions") or 0)
    clicks = int(values.get("clicks") or 0)
    registrations = int(values.get("registrations") or 0)
    ftds = int(values.get("ftds") or 0)
    return {
        "spend": _decimal_string(spend),
        "impressions": impressions,
        "clicks": clicks,
        "leads": int(values.get("leads") or 0),
        "registrations": registrations,
        "ftds": ftds,
        "confirmed_deposits": int(values.get("confirmed_deposits") or 0),
        "redeposits": int(values.get("redeposits") or 0),
        "revenue": _decimal_string(revenue),
        "cpc": _ratio(spend, clicks, percent=False),
        "ctr_pct": _ratio(clicks, impressions, percent=True),
        "click_registration_cr_pct": _ratio(registrations, clicks, percent=True),
        "registration_ftd_cr_pct": _ratio(ftds, registrations, percent=True),
        "cost_per_registration": _ratio(spend, registrations, percent=False),
        "cost_per_ftd": _ratio(spend, ftds, percent=False),
        "roi_pct": _ratio(revenue - spend, spend, percent=True),
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
                   COALESCE(SUM(spend), 0) AS spend,
                   COALESCE(SUM(impressions), 0)::bigint AS impressions,
                   COALESCE(SUM(clicks), 0)::bigint AS clicks,
                   COALESCE(SUM(leads), 0)::bigint AS leads,
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
                       WHERE lower(trim(event_type)) IN ('redeposit', 'redep', 'cpa_redep')
                   )::bigint AS redeposits,
                   COALESCE(SUM(COALESCE(revenue, 0)) FILTER (
                       WHERE lower(trim(event_type)) NOT IN (
                           'registration', 'reg', 'signup',
                           'decline', 'declined', 'rejected', 'trash', 'baddep'
                       )
                   ), 0) AS revenue,
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
            o.id AS offer_id, o.code AS offer_code,
            r.cpa_threshold, r.stop_percent_of_rule,
            COALESCE(m.spend, 0) AS spend,
            COALESCE(m.impressions, 0)::bigint AS impressions,
            COALESCE(m.clicks, 0)::bigint AS clicks,
            COALESCE(m.leads, 0)::bigint AS leads,
            COALESCE(ts.registrations, 0)::bigint AS registrations,
            COALESCE(ts.ftds, 0)::bigint AS ftds,
            COALESCE(ts.confirmed_deposits, 0)::bigint AS confirmed_deposits,
            COALESCE(te.redeposits, 0)::bigint AS redeposits,
            COALESCE(te.revenue, 0) AS revenue,
            m.meta_last_at,
            GREATEST(ts.tracker_last_at, te.tracker_event_last_at) AS tracker_last_at
        FROM fb_ads a
        JOIN fb_adsets s ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        LEFT JOIN offers o ON o.id = c.offer_id
        LEFT JOIN offer_rules r ON r.offer_id = o.id
        LEFT JOIN meta_by_ad m ON m.ad_id = a.id
        LEFT JOIN tracker_state ts ON ts.ad_id = a.id
        LEFT JOIN tracker_events te ON te.ad_id = a.id
        WHERE {catalog_where}
          AND (m.ad_id IS NOT NULL OR ts.ad_id IS NOT NULL OR te.ad_id IS NOT NULL)
        ORDER BY c.campaign_name, s.adset_name, a.ad_name
        LIMIT 50000
        """
    )
    params = {
        "from_dt": from_dt,
        "to_dt": to_dt,
        **filter_params,
        **meta_boundary_params,
        **tracker_boundary_params,
        **event_boundary_params,
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
                "budgets": [],
                "unavailable_budgets": 0,
            },
        )
        if identity["child_id"]:
            group["children"].add(identity["child_id"])
        if row.get("ad_account_id"):
            group["ad_account_ids"].add(str(row["ad_account_id"]))
        if row.get("offer_id"):
            group["offer_ids"].add(str(row["offer_id"]))
        if row.get("offer_code"):
            group["offer_codes"].add(str(row["offer_code"]))
        for key in (
            "impressions",
            "clicks",
            "leads",
            "registrations",
            "ftds",
            "confirmed_deposits",
            "redeposits",
        ):
            group[key] += int(row.get(key) or 0)
        group["spend"] += _decimal(row.get("spend"))
        group["revenue"] += _decimal(row.get("revenue"))

        if is_live:
            budget = calculate_live_budget(
                actual_spend=_decimal(row.get("spend")),
                cpa_threshold=row.get("cpa_threshold"),
                stop_percent_of_rule=row.get("stop_percent_of_rule"),
                clicks=int(row.get("clicks") or 0),
                leads=int(row.get("leads") or 0),
                registrations=int(row.get("registrations") or 0),
                confirmed_deposits=int(row.get("confirmed_deposits") or 0),
            )
            if budget is None:
                group["unavailable_budgets"] += 1
            else:
                group["budgets"].append(budget)

    rows: list[dict[str, Any]] = []
    for group in groups.values():
        metrics = _metrics_payload(group)
        budgets: list[LiveBudget] = group.pop("budgets")
        unavailable = int(group.pop("unavailable_budgets"))
        live_budget = None
        budget_reason = None
        if is_live and unavailable:
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
                "offer_id": next(iter(group["offer_ids"]), None)
                if len(group["offer_ids"]) <= 1
                else None,
                "offer_code": next(iter(group["offer_codes"]), None)
                if len(group["offer_codes"]) <= 1
                else "Несколько",
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

    def row_sort_value(row: dict[str, Any]) -> Any:
        if sort_key == "base_delta":
            budget = row.get("live_budget") or {}
            return _decimal(budget.get("base_delta"))
        if sort_key in {"spend", "revenue"}:
            return _decimal(row.get(sort_key))
        return row.get(sort_key) or 0

    rows.sort(key=row_sort_value, reverse=direction == "desc")
    total = len(rows)
    start = (page - 1) * page_size
    paged = rows[start : start + page_size]

    totals_acc: dict[str, Any] = defaultdict(int)
    totals_acc["spend"] = sum((_decimal(r.get("spend")) for r in rows), Decimal("0"))
    totals_acc["revenue"] = sum((_decimal(r.get("revenue")) for r in rows), Decimal("0"))
    for key in (
        "impressions",
        "clicks",
        "leads",
        "registrations",
        "ftds",
        "confirmed_deposits",
        "redeposits",
    ):
        totals_acc[key] = sum(int(r.get(key) or 0) for r in rows)

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
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": math.ceil(total / page_size) if total else 0,
        },
    }


async def fetch_source_quality(
    engine: AsyncEngine, *, from_dt: datetime, to_dt: datetime
) -> dict[str, dict[str, Any]]:
    sql = text(
        """
        SELECT
          (SELECT MAX(cycle_ts) FROM ad_metrics WHERE cycle_ts BETWEEN :from_dt AND :to_dt)
            AS meta_last_at,
          (SELECT MAX(occurred_at) FROM adsetpro_postback_events
             WHERE occurred_at BETWEEN :from_dt AND :to_dt) AS tracker_last_at,
          (SELECT COUNT(*) FROM adsetpro_postback_events
             WHERE occurred_at BETWEEN :from_dt AND :to_dt
               AND attribution_status NOT LIKE 'matched%')::bigint AS unmatched_events,
          (SELECT COUNT(*) FROM adsetpro_postback_events
             WHERE occurred_at BETWEEN :from_dt AND :to_dt)::bigint AS tracker_events
        """
    )
    async with engine.connect() as conn:
        row = (await conn.execute(sql, {"from_dt": from_dt, "to_dt": to_dt})).mappings().one()

    now = datetime.now(UTC)

    def lag(ts: datetime | None) -> int | None:
        if ts is None:
            return None
        return max(0, int((now - ts).total_seconds()))

    meta_last = row["meta_last_at"]
    tracker_last = row["tracker_last_at"]
    unmatched = int(row["unmatched_events"] or 0)
    tracker_events = int(row["tracker_events"] or 0)
    meta_status = (
        "missing" if meta_last is None else ("good" if (lag(meta_last) or 0) <= 900 else "degraded")
    )
    if tracker_events == 0:
        tracker_status = "unknown"
        tracker_note = "В выбранном окне не было postback-событий"
    elif unmatched > 0:
        tracker_status = "degraded"
        tracker_note = f"Не атрибутировано событий: {unmatched}"
    else:
        tracker_status = "good"
        tracker_note = "AdSet.pro — источник регистраций и депозитов"
    return {
        "meta": {
            "source": "meta",
            "status": meta_status,
            "last_event_at": meta_last,
            "lag_seconds": lag(meta_last),
            "note": "Spend, impressions и clicks из Meta",
        },
        "tracker": {
            "source": "tracker",
            "status": tracker_status,
            "last_event_at": tracker_last,
            "lag_seconds": lag(tracker_last),
            "unmatched_events": unmatched,
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
            m.spend, m.clicks, m.leads,
            r.cpa_threshold, r.stop_percent_of_rule
        FROM ad_metrics m
        JOIN fb_ads a ON a.id = m.ad_id
        JOIN fb_adsets s ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        LEFT JOIN offers o ON o.id = c.offer_id
        LEFT JOIN offer_rules r ON r.offer_id = o.id
        WHERE m.cycle_ts BETWEEN :from_dt AND :to_dt
          AND m.cycle_ts >= ({metric_boundary})
          AND {catalog_where}
        ORDER BY m.ad_id, date_trunc('hour', m.cycle_ts), m.cycle_ts DESC
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
    latest_by_ad: dict[Any, dict[str, Any]] = {}
    points: list[dict[str, Any]] = []
    cursor = start_hour
    while cursor <= end_hour:
        for row in by_hour.get(cursor, []):
            latest_by_ad[row["ad_id"]] = row
        point_end = min(cursor + timedelta(hours=1), to_dt)
        budgets: list[LiveBudget] = []
        unavailable = 0
        for ad_id, row in latest_by_ad.items():
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
            budget = calculate_live_budget(
                actual_spend=_decimal(row.get("spend")),
                cpa_threshold=row.get("cpa_threshold"),
                stop_percent_of_rule=row.get("stop_percent_of_rule"),
                clicks=int(row.get("clicks") or 0),
                leads=int(row.get("leads") or 0),
                registrations=registrations,
                confirmed_deposits=confirmed,
            )
            if budget is None:
                unavailable += 1
            else:
                budgets.append(budget)
        if latest_by_ad:
            points.append(
                {
                    "ts": cursor,
                    "actual": _decimal_string(
                        sum((_decimal(r.get("spend")) for r in latest_by_ad.values()), Decimal("0"))
                    ),
                    "base": _decimal_string(sum((b.base_budget for b in budgets), Decimal("0"))),
                    "stop": _decimal_string(sum((b.stop_budget for b in budgets), Decimal("0"))),
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
) -> list[dict[str, int]]:
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
        WITH hourly AS (
            SELECT DISTINCT ON (m.ad_id, date_trunc('hour', m.cycle_ts))
                m.ad_id, date_trunc('hour', m.cycle_ts) AS bucket_ts,
                COALESCE(m.clicks, 0)::bigint AS clicks
            FROM ad_metrics m
            JOIN fb_ads a ON a.id = m.ad_id
            JOIN fb_adsets s ON s.id = a.adset_id
            JOIN fb_campaigns c ON c.id = s.campaign_id
            LEFT JOIN offers o ON o.id = c.offer_id
            WHERE m.cycle_ts BETWEEN :baseline_from AND :to_dt
              AND {catalog_where}
            ORDER BY m.ad_id, date_trunc('hour', m.cycle_ts), m.cycle_ts DESC
        ), with_previous AS (
            SELECT ad_id, bucket_ts, clicks,
                   LAG(clicks) OVER (PARTITION BY ad_id ORDER BY bucket_ts) AS previous_clicks
            FROM hourly
        )
        SELECT
            EXTRACT(ISODOW FROM timezone(:timezone_name, bucket_ts))::int AS weekday,
            EXTRACT(HOUR FROM timezone(:timezone_name, bucket_ts))::int AS hour,
            SUM(CASE
                WHEN previous_clicks IS NULL THEN clicks
                WHEN clicks >= previous_clicks THEN clicks - previous_clicks
                ELSE clicks
            END)::bigint AS clicks
        FROM with_previous
        WHERE bucket_ts BETWEEN :from_dt AND :to_dt
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
            WHERE t.registration_at BETWEEN :from_dt AND :to_dt AND {catalog_where}
            UNION ALL
            SELECT t.ftd_at AS event_ts, 0 AS registrations, 1 AS ftds
            FROM tracker_click_state t
            JOIN fb_ads a ON a.id = t.ad_id
            JOIN fb_adsets s ON s.id = a.adset_id
            JOIN fb_campaigns c ON c.id = s.campaign_id
            LEFT JOIN offers o ON o.id = c.offer_id
            WHERE t.ftd_at BETWEEN :from_dt AND :to_dt AND {catalog_where}
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
    cells: dict[tuple[int, int], dict[str, int]] = {}
    for weekday in range(1, 8):
        for hour in range(24):
            cells[(weekday, hour)] = {
                "weekday": weekday,
                "hour": hour,
                "clicks": 0,
                "registrations": 0,
                "ftds": 0,
            }
    for row in click_rows:
        cells[(int(row["weekday"]), int(row["hour"]))]["clicks"] = int(row["clicks"] or 0)
    for row in tracker_rows:
        cell = cells[(int(row["weekday"]), int(row["hour"]))]
        cell["registrations"] = int(row["registrations"] or 0)
        cell["ftds"] = int(row["ftds"] or 0)
    return list(cells.values())


__all__ = [
    "aggregate_performance",
    "fetch_daypart_cells",
    "fetch_filter_options",
    "fetch_live_budget_points",
    "fetch_performance_rows",
    "fetch_source_quality",
]
