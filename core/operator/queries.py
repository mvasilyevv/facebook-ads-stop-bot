"""Bounded, server-side queries for the operator API.

All metric reads preserve cumulative Meta semantics: the latest snapshot per ad
is selected before values are aggregated or displayed.  No unknown value is
coerced into zero at this layer.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.tasks.channel import disable_channel_sql, enable_channel_sql, target_id_sql


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def task_action_state(status: str, result: Any) -> str:
    """Map storage lifecycle to the strict public action state."""
    result_payload = _json(result)
    outcome = str(result_payload.get("outcome") or "").upper()
    if outcome == "UNKNOWN" or result_payload.get("reconcile_required") is True:
        return "unknown"
    if status in {"pending", "retrying"}:
        return "queued"
    if status == "running":
        return "running"
    if status == "cancelled":
        return "cancelled"
    if status == "succeeded":
        return "confirmed" if outcome == "CONFIRMED" else "unknown"
    return "failed"


def task_action_reason(state: str) -> str:
    """Return deterministic operator copy without exposing task diagnostics."""
    return {
        "queued": "Команда принята и ожидает выполнения.",
        "running": "Команда выполняется; итог ещё не подтверждён.",
        "confirmed": "Результат команды подтверждён.",
        "failed": "Команда завершилась ошибкой. Проверьте состояние перед повтором.",
        "cancelled": "Команда отменена.",
        "unknown": "Результат команды требует сверки. Не повторяйте действие вслепую.",
    }.get(
        state,
        "Состояние команды требует сверки. Не повторяйте действие вслепую.",
    )


def task_action_kind(task_type: str, payload: Any) -> str:
    body = _json(payload)
    mutation = str(body.get("mutation_kind") or "")
    if mutation == "pause_ad":
        return "pause"
    if mutation == "activate_ad":
        return "activate"
    if task_type == "observer_scan":
        return "scan"
    if task_type == "campaign_create":
        return "create"
    if mutation == "duplicate_adset_structure":
        return "duplicate"
    return "other"


def _task_title(kind: str) -> str:
    return {
        "pause": "Отключение рекламы",
        "activate": "Включение рекламы",
        "scan": "Сканирование",
        "create": "Создание кампании",
        "duplicate": "Дублирование",
        "other": "Системное действие",
    }[kind]


def _task_item(row: Any) -> dict[str, Any]:
    payload = _json(row.payload)
    result = _json(row.result)
    kind = task_action_kind(str(row.task_type), payload)
    state = task_action_state(str(row.status), result)
    target = row.target_label or payload.get("target_id") or payload.get("ad_id")
    payload_target_id = payload.get("target_id")
    return {
        "id": str(row.id),
        "public_id": f"#{row.id}",
        "kind": kind,
        "state": state,
        "title": _task_title(kind),
        "target_id": (
            str(payload_target_id)
            if isinstance(payload_target_id, (str, int))
            and not isinstance(payload_target_id, bool)
            and str(payload_target_id).strip()
            else None
        ),
        "target_label": str(target) if target else None,
        "requested_at": row.created_at,
        "updated_at": row.updated_at,
        "requested_by": str(row.requested_by) if row.requested_by else None,
        "reason": task_action_reason(state),
        "correlation_id": str(row.correlation_id),
        "account_id": (
            str(payload.get("account_id") or payload.get("ad_account_id"))
            if payload.get("account_id") or payload.get("ad_account_id")
            else None
        ),
        "currency": (str(payload.get("currency")) if payload.get("currency") else None),
        "cabinet_timezone": (
            str(payload.get("cabinet_timezone")) if payload.get("cabinet_timezone") else None
        ),
        "account_context_observed_at": payload.get("account_context_observed_at"),
        "account_context_issues": [
            str(issue) for issue in (payload.get("account_context_issues") or [])
        ],
    }


async def fetch_operator_actions(
    engine: AsyncEngine,
    *,
    limit: int = 20,
    before_id: int | None = None,
    states: tuple[str, ...] = (),
    account_id: str | None = None,
) -> tuple[list[dict[str, Any]], int | None, datetime | None]:
    """Fetch action lifecycle rows with an immutable-id cursor."""
    clauses = ["TRUE"]
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 100)) + 1}
    if before_id is not None:
        clauses.append("tq.id < :before_id")
        params["before_id"] = int(before_id)
    if account_id:
        clauses.append(
            """
            (CASE
                WHEN NULLIF(tq.payload->>'account_id', '') IS NOT NULL
                    THEN REGEXP_REPLACE(tq.payload->>'account_id', '^act_', '')
                WHEN NULLIF(tq.payload->>'ad_account_id', '') IS NOT NULL
                    THEN REGEXP_REPLACE(tq.payload->>'ad_account_id', '^act_', '')
                ELSE c.ad_account_id
             END) = :account_id
            """
        )
        params["account_id"] = account_id.removeprefix("act_")
    public_states = {
        state
        for state in states
        if state in {"queued", "running", "confirmed", "failed", "cancelled", "unknown"}
    }
    if public_states:
        clauses.append(
            """
            (CASE
                WHEN UPPER(COALESCE(tq.result->>'outcome', '')) = 'UNKNOWN'
                  OR COALESCE(tq.result->>'reconcile_required', 'false') = 'true'
                  OR (
                    tq.status = 'succeeded'
                    AND UPPER(COALESCE(tq.result->>'outcome', '')) <> 'CONFIRMED'
                  )
                    THEN 'unknown'
                WHEN tq.status IN ('pending','retrying') THEN 'queued'
                WHEN tq.status = 'running' THEN 'running'
                WHEN tq.status = 'cancelled' THEN 'cancelled'
                WHEN tq.status = 'succeeded'
                 AND UPPER(COALESCE(tq.result->>'outcome', '')) = 'CONFIRMED'
                    THEN 'confirmed'
                ELSE 'failed'
             END) IN :public_states
            """
        )
        params["public_states"] = sorted(public_states)
    stmt = text(
        f"""
        SELECT tq.id, tq.task_type, tq.status, tq.payload, tq.result,
               tq.requested_by, tq.last_error, tq.created_at, tq.updated_at,
               tq.correlation_id,
               COALESCE(a.ad_name, tq.payload->>'target_id') AS target_label
        FROM task_queue tq
        LEFT JOIN fb_ads a ON a.fb_ad_id = tq.payload->>'target_id'
        LEFT JOIN fb_adsets s ON s.id = a.adset_id
        LEFT JOIN fb_campaigns c ON c.id = s.campaign_id
        WHERE {" AND ".join(clauses)}
        ORDER BY tq.id DESC
        LIMIT :limit
        """
    )
    if "public_states" in params:
        stmt = stmt.bindparams(bindparam("public_states", expanding=True))
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt, params)).all()
    page_size = params["limit"] - 1
    has_more = len(rows) > page_size
    visible = rows[:page_size]
    items = [_task_item(row) for row in visible]
    next_cursor = int(visible[-1].id) if has_more and visible else None
    as_of = max((row.updated_at for row in visible), default=None)
    return items, next_cursor, as_of


async def fetch_operator_events(
    engine: AsyncEngine,
    *,
    from_dt: datetime,
    to_dt: datetime,
    limit: int,
    campaign_uuid: uuid.UUID | None = None,
    fb_ad_id: str | None = None,
    stage: str | None = None,
    task_status: str | None = None,
    search: str | None = None,
) -> list[Any]:
    """Read alerts and terminal money actions as one bounded operator feed."""
    target_expr = target_id_sql("tq")
    toggle_pred = f"({disable_channel_sql('tq')} OR {enable_channel_sql('tq')})"
    common_campaign = "AND c.id = :campaign_uuid" if campaign_uuid else ""
    common_ad = "AND a.fb_ad_id = :fb_ad_id" if fb_ad_id else ""
    alert_stage = "AND lower(ae.stage) = lower(:stage)" if stage else ""
    alert_task_guard = "AND false" if task_status else ""
    task_status_filter = "AND lower(tq.status) = lower(:task_status)" if task_status else ""
    task_stage_guard = "AND false" if stage else ""
    alert_search = (
        "AND (a.fb_ad_id ILIKE :search OR a.ad_name ILIKE :search OR c.campaign_name ILIKE :search)"
        if search
        else ""
    )
    task_search = (
        f"AND ({target_expr} ILIKE :search OR a.ad_name ILIKE :search "
        "OR c.campaign_name ILIKE :search)"
        if search
        else ""
    )
    query = text(
        f"""
        SELECT
            'alert'         AS event_type,
            ae.created_at   AS ts,
            a.fb_ad_id,
            a.ad_name,
            c.id::text      AS campaign_id,
            c.campaign_name,
            ae.stage,
            ae.matched_rule_codes::text AS rule_codes_raw,
            NULL::text      AS task_type,
            NULL::text      AS task_status
        FROM alert_events ae
        JOIN fb_ads a ON a.id = ae.ad_id
        JOIN fb_adsets s ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        WHERE ae.created_at BETWEEN :from_dt AND :to_dt
          {common_campaign}
          {common_ad}
          {alert_stage}
          {alert_task_guard}
          {alert_search}

        UNION ALL

        SELECT
            'task'          AS event_type,
            tq.updated_at   AS ts,
            {target_expr}   AS fb_ad_id,
            a.ad_name,
            c.id::text      AS campaign_id,
            c.campaign_name,
            NULL            AS stage,
            NULL            AS rule_codes_raw,
            tq.task_type,
            tq.status       AS task_status
        FROM task_queue tq
        LEFT JOIN fb_ads a ON a.fb_ad_id = {target_expr}
        LEFT JOIN fb_adsets s ON s.id = a.adset_id
        LEFT JOIN fb_campaigns c ON c.id = s.campaign_id
        WHERE {toggle_pred}
          AND tq.status IN ('succeeded', 'failed', 'cancelled')
          AND tq.updated_at BETWEEN :from_dt AND :to_dt
          {common_campaign}
          {common_ad}
          {task_status_filter}
          {task_stage_guard}
          {task_search}

        ORDER BY ts DESC
        LIMIT :limit
        """
    )
    params: dict[str, Any] = {
        "from_dt": from_dt,
        "to_dt": to_dt,
        "limit": max(1, min(limit, 1000)),
    }
    if campaign_uuid:
        params["campaign_uuid"] = campaign_uuid
    if fb_ad_id:
        params["fb_ad_id"] = fb_ad_id
    if stage:
        params["stage"] = stage
    if task_status:
        params["task_status"] = task_status
    if search:
        params["search"] = f"%{search.strip()}%"
    async with engine.connect() as conn:
        return (await conn.execute(query, params)).fetchall()


async def fetch_operator_incidents(
    engine: AsyncEngine,
    *,
    account_id: str | None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses = ["i.status IN ('open','acknowledged','executing','failed')"]
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
    if account_id:
        clauses.append("i.ad_account_id = :account_id")
        params["account_id"] = account_id.removeprefix("act_")
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    f"""
                    SELECT i.id, i.severity, i.status, i.title, i.summary,
                           i.resource_type, i.resource_id, i.opened_at,
                           i.correlation_id, i.facts,
                           CASE WHEN i.resource_type IN ('ad','fb_ad')
                                THEN a.ad_name ELSE NULL END AS resource_label
                    FROM incidents i
                    LEFT JOIN fb_ads a
                      ON i.resource_type IN ('ad','fb_ad')
                     AND a.fb_ad_id = i.resource_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY
                      CASE i.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                      i.opened_at DESC, i.id
                    LIMIT :limit
                    """
                ),
                params,
            )
        ).all()
    return [dict(row._mapping) for row in rows]


async def fetch_operator_incident_page(
    engine: AsyncEngine,
    *,
    account_id: str | None,
    severities: tuple[str, ...] = (),
    statuses: tuple[str, ...] = (),
    page: int = 1,
    page_size: int = 30,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch a bounded incident journal with deterministic secondary ordering."""

    safe_page = max(1, int(page))
    safe_page_size = max(1, min(int(page_size), 100))
    clauses = ["TRUE"]
    params: dict[str, Any] = {
        "limit": safe_page_size,
        "offset": (safe_page - 1) * safe_page_size,
    }
    if account_id:
        clauses.append("i.ad_account_id = :account_id")
        params["account_id"] = account_id.removeprefix("act_")

    allowed_severities = tuple(
        sorted({value for value in severities if value in {"ok", "warning", "critical", "unknown"}})
    )
    if allowed_severities:
        clauses.append("i.severity IN :severities")
        params["severities"] = allowed_severities

    allowed_statuses = tuple(
        sorted(
            {
                value
                for value in statuses
                if value in {"open", "acknowledged", "executing", "resolved", "failed"}
            }
        )
    )
    if allowed_statuses:
        clauses.append("i.status IN :statuses")
        params["statuses"] = allowed_statuses

    where_sql = " AND ".join(clauses)
    page_stmt = text(
        f"""
        SELECT i.id, i.severity, i.status, i.title, i.summary,
               i.resource_type, i.resource_id, i.opened_at,
               i.correlation_id, i.facts, i.ad_account_id,
               CASE WHEN i.resource_type IN ('ad','fb_ad')
                    THEN a.ad_name ELSE NULL END AS resource_label
        FROM incidents i
        LEFT JOIN fb_ads a
          ON i.resource_type IN ('ad','fb_ad')
         AND a.fb_ad_id = i.resource_id
        WHERE {where_sql}
        ORDER BY
          CASE i.severity
            WHEN 'critical' THEN 0
            WHEN 'warning' THEN 1
            WHEN 'unknown' THEN 2
            ELSE 3
          END,
          i.opened_at DESC,
          i.id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    count_stmt = text(f"SELECT COUNT(*) FROM incidents i WHERE {where_sql}")
    for key in ("severities", "statuses"):
        if key in params:
            page_stmt = page_stmt.bindparams(bindparam(key, expanding=True))
            count_stmt = count_stmt.bindparams(bindparam(key, expanding=True))

    async with engine.connect() as conn:
        rows = (await conn.execute(page_stmt, params)).all()
        total = int((await conn.execute(count_stmt, params)).scalar_one())
    return [dict(row._mapping) for row in rows], total


async def fetch_operator_incident(
    engine: AsyncEngine,
    *,
    incident_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Fetch one incident without inheriting the ranked attention-feed limit."""

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT i.id, i.severity, i.status, i.title, i.summary,
                           i.resource_type, i.resource_id, i.opened_at,
                           i.correlation_id, i.facts, i.ad_account_id,
                           CASE WHEN i.resource_type IN ('ad','fb_ad')
                                THEN a.ad_name ELSE NULL END AS resource_label
                    FROM incidents i
                    LEFT JOIN fb_ads a
                      ON i.resource_type IN ('ad','fb_ad')
                     AND a.fb_ad_id = i.resource_id
                    WHERE i.id = :incident_id
                    LIMIT 1
                    """
                ),
                {"incident_id": incident_id},
            )
        ).first()
    return dict(row._mapping) if row is not None else None


async def fetch_operator_scan_state(
    engine: AsyncEngine,
    *,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Read DB-authoritative scan freshness and actor stages for one scope."""
    params = {"account_id": account_id}
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    WITH latest_scan AS (
                      SELECT started_at, outcome
                      FROM scan_runs
                      WHERE started_at >= NOW() - INTERVAL '7 days'
                        AND (
                          CAST(:account_id AS TEXT) IS NULL
                          OR ad_account_id = CAST(:account_id AS TEXT)
                        )
                      ORDER BY started_at DESC, id DESC
                      LIMIT 1
                    )
                    SELECT
                      (SELECT started_at FROM latest_scan) AS last_scan_at,
                      (SELECT outcome FROM latest_scan) AS last_scan_outcome,
                      (
                        SELECT MIN(next_scan_at)
                        FROM cabinet_runtime
                        WHERE CAST(:account_id AS TEXT) IS NULL
                           OR ad_account_id = CAST(:account_id AS TEXT)
                      ) AS next_scan_at,
                      (SELECT BOOL_OR(is_scanning_enabled) FROM observer_config) AS enabled
                    """
                ),
                params,
            )
        ).one()
        actors = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT ad_account_id, owner_instance, lease_token,
                           lease_expires_at, stage, last_progress_at,
                           last_snapshot_at, last_error_code AS error
                    FROM cabinet_runtime
                    WHERE CAST(:account_id AS TEXT) IS NULL
                       OR ad_account_id = CAST(:account_id AS TEXT)
                    ORDER BY ad_account_id
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
    return {**dict(row._mapping), "actors": [dict(actor) for actor in actors]}


async def fetch_operator_revision(engine: AsyncEngine) -> tuple[int, str]:
    """Return a commit-visible, non-decreasing PostgreSQL WAL cursor.

    Identity values from ``operator_revision_events`` are intentionally not a
    commit cursor: concurrent transactions can allocate 100/101 and commit in
    the opposite order.  WAL always advances for the later commit record, so a
    lost NOTIFY is still detected by the websocket heartbeat.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT COALESCE(
                        pg_wal_lsn_diff(
                            CASE
                                WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn()
                                ELSE pg_current_wal_lsn()
                            END,
                            '0/0'::pg_lsn
                        ),
                        0
                    )::bigint AS revision
                    """
                )
            )
        ).one()
    sequence = max(0, int(row.revision))
    revision = f"r{sequence:x}"
    return sequence, revision


_AD_SORT: dict[str, str] = {
    "name": "LOWER({alias}.ad_name)",
    "spend": "{alias}.spend",
    "clicks": "{alias}.clicks",
    "registrations": "{alias}.registrations",
    "ftd": "{alias}.ftds",
    "updated": "{alias}.cycle_ts",
}


async def fetch_operator_ads(
    engine: AsyncEngine,
    *,
    from_dt: datetime,
    to_dt: datetime,
    account_id: str | None,
    search: str | None,
    delivery_status: str | None,
    severity: str | None,
    sort: str,
    direction: Literal["asc", "desc"],
    page: int,
    page_size: int,
    tracker_available: bool,
) -> dict[str, Any]:
    """Server-side ad search/filter/sort/page with stable UUID tie-breaking."""
    observed_at = datetime.now(UTC)
    stale_before = observed_at - timedelta(seconds=60)
    clauses = ["a.last_seen_at >= :catalog_since"]
    params: dict[str, Any] = {
        "from_dt": from_dt,
        "to_dt": to_dt,
        "catalog_since": to_dt.replace(tzinfo=UTC) if to_dt.tzinfo is None else to_dt,
        "limit": page_size,
        "offset": (page - 1) * page_size,
        "stale_before": stale_before,
    }
    # Keep catalog rows seen during the last 90 days while metrics remain window-bound.
    params["catalog_since"] = to_dt - timedelta(days=90)
    if account_id:
        clauses.append("c.ad_account_id = :account_id")
        params["account_id"] = account_id.removeprefix("act_")
    if search:
        clauses.append(
            "(a.ad_name ILIKE :search OR a.fb_ad_id ILIKE :search "
            "OR s.adset_name ILIKE :search OR c.campaign_name ILIKE :search)"
        )
        params["search"] = f"%{search.strip()}%"
    if delivery_status:
        clauses.append("a.delivery_status = :delivery_status")
        params["delivery_status"] = delivery_status
    severity_expr = (
        "CASE WHEN alert.alert_state IN ('stop_sent','disabled') THEN 'critical' "
        "WHEN alert.alert_state = 'warning_sent' THEN 'warning' "
        "WHEN m.cycle_ts IS NULL OR m.cycle_ts < :stale_before "
        "OR m.spend IS NULL OR m.impressions IS NULL OR m.clicks IS NULL "
        "THEN 'unknown' "
        "ELSE 'ok' END"
    )
    params["severity"] = severity

    sort_template = _AD_SORT.get(sort, _AD_SORT["updated"])
    inner_sort_expr = sort_template.format(alias="candidate")
    outer_sort_expr = sort_template.format(alias="page_rows")
    nulls = "NULLS FIRST" if direction == "asc" else "NULLS LAST"
    sql = text(
        f"""
        WITH meta_latest AS (
          SELECT DISTINCT ON (m.ad_id)
                 m.ad_id, m.cycle_ts, m.spend, m.impressions, m.clicks
          FROM ad_metrics m
          WHERE m.cycle_ts BETWEEN :from_dt AND :to_dt
          ORDER BY m.ad_id, m.cycle_ts DESC
        ), tracker AS (
          SELECT t.ad_id,
                 COUNT(*) FILTER (WHERE t.registration_at BETWEEN :from_dt AND :to_dt)::int
                   AS registrations,
                 COUNT(*) FILTER (WHERE t.ftd_at BETWEEN :from_dt AND :to_dt)::int AS ftds,
                 COUNT(*) FILTER (
                   WHERE t.confirmed_deposit_at BETWEEN :from_dt AND :to_dt
                 )::int AS confirmed_deposits
          FROM tracker_click_state t
          WHERE t.ad_id IS NOT NULL
            AND (t.last_event_at BETWEEN :from_dt AND :to_dt
                 OR t.registration_at BETWEEN :from_dt AND :to_dt
                 OR t.ftd_at BETWEEN :from_dt AND :to_dt
                 OR t.confirmed_deposit_at BETWEEN :from_dt AND :to_dt)
          GROUP BY t.ad_id
        ), base AS (
        SELECT a.id, a.fb_ad_id, a.ad_name, a.delivery_status,
               s.id AS adset_id, s.adset_name,
               c.id AS campaign_id, c.campaign_name, c.ad_account_id,
               m.cycle_ts, m.spend, m.impressions, m.clicks,
               tracker.registrations, tracker.ftds, tracker.confirmed_deposits,
               alert.alert_state,
               {severity_expr} AS derived_severity,
               active_task.id AS active_task_id,
               active_task.task_type AS active_task_type,
               active_task.status AS active_task_status,
               active_task.payload AS active_task_payload,
               active_task.result AS active_task_result,
               active_task.requested_by AS active_task_requested_by,
               active_task.last_error AS active_task_last_error,
               active_task.created_at AS active_task_created_at,
               active_task.updated_at AS active_task_updated_at,
               active_task.correlation_id AS active_task_correlation_id
        FROM fb_ads a
        JOIN fb_adsets s ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        LEFT JOIN meta_latest m ON m.ad_id = a.id
        LEFT JOIN tracker ON tracker.ad_id = a.id
        LEFT JOIN ad_alert_state alert ON alert.ad_id = a.id
        LEFT JOIN LATERAL (
          SELECT latest_task.id, latest_task.task_type, latest_task.status,
                 latest_task.payload, latest_task.result,
                 latest_task.requested_by, latest_task.last_error,
                 latest_task.created_at, latest_task.updated_at,
                 latest_task.correlation_id
          FROM (
            SELECT tq.id, tq.task_type, tq.status, tq.payload, tq.result,
                   tq.requested_by, tq.last_error, tq.created_at, tq.updated_at,
                   tq.completed_at, tq.correlation_id
            FROM task_queue tq
            WHERE tq.payload->>'target_id' = a.fb_ad_id
              AND tq.task_type = 'meta_api_mutation'
              AND tq.payload->>'mutation_kind' IN ('pause_ad', 'activate_ad')
            ORDER BY tq.id DESC
            LIMIT 1
          ) AS latest_task
          WHERE (
              latest_task.status IN ('pending','retrying','running')
              OR (
                latest_task.status IN ('succeeded','failed','cancelled')
                AND (
                  UPPER(COALESCE(latest_task.result->>'outcome', '')) = 'UNKNOWN'
                  OR COALESCE(
                    latest_task.result->>'reconcile_required', 'false'
                  ) = 'true'
                )
              )
              OR (
                latest_task.status = 'succeeded'
                AND UPPER(COALESCE(
                  latest_task.result->>'outcome', ''
                )) = 'CONFIRMED'
                AND NOT EXISTS (
                  SELECT 1
                  FROM ad_metrics AS observed_metric
                  WHERE observed_metric.ad_id = a.id
                    AND observed_metric.cycle_ts >
                        COALESCE(
                          latest_task.completed_at,
                          latest_task.updated_at
                        )
                )
              )
            )
        ) active_task ON TRUE
        WHERE {" AND ".join(clauses)}
        ), quality AS (
          SELECT COUNT(*)::int AS quality_total,
                 COUNT(*) FILTER (WHERE cycle_ts IS NULL)::int
                   AS unavailable_count,
                 COUNT(*) FILTER (
                   WHERE cycle_ts IS NOT NULL AND cycle_ts < :stale_before
                 )::int AS stale_count,
                 COUNT(*) FILTER (
                   WHERE cycle_ts IS NOT NULL
                     AND cycle_ts >= :stale_before
                     AND (spend IS NULL OR impressions IS NULL OR clicks IS NULL)
                 )::int AS incomplete_count,
                 MIN(cycle_ts) AS section_as_of
          FROM base
        ), filtered_count AS (
          SELECT COUNT(*)::int AS total_count
          FROM base
          WHERE (
            CAST(:severity AS TEXT) IS NULL
            OR derived_severity = CAST(:severity AS TEXT)
          )
        )
        SELECT page_rows.*, filtered_count.total_count,
               quality.quality_total, quality.unavailable_count,
               quality.stale_count, quality.incomplete_count,
               quality.section_as_of
        FROM quality
        CROSS JOIN filtered_count
        LEFT JOIN LATERAL (
          SELECT candidate.*
          FROM base candidate
          WHERE (
            CAST(:severity AS TEXT) IS NULL
            OR candidate.derived_severity = CAST(:severity AS TEXT)
          )
          ORDER BY {inner_sort_expr} {direction.upper()} {nulls},
                   candidate.id {direction.upper()}
          LIMIT :limit OFFSET :offset
        ) page_rows ON TRUE
        ORDER BY {outer_sort_expr} {direction.upper()} {nulls},
                 page_rows.id {direction.upper()}
        """
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(sql, params)).all()
    result_rows: list[dict[str, Any]] = []
    for row in rows:
        if row.id is None:
            continue
        cycle_ts = row.cycle_ts
        if cycle_ts is None:
            data_state = "unavailable"
        elif cycle_ts < stale_before:
            data_state = "stale"
        elif row.spend is None or row.impressions is None or row.clicks is None:
            data_state = "partial"
        else:
            data_state = "ready"
        row_severity = (
            "critical"
            if row.alert_state in {"stop_sent", "disabled"}
            else (
                "warning"
                if row.alert_state == "warning_sent"
                else "unknown"
                if data_state != "ready"
                else "ok"
            )
        )
        registrations = int(row.registrations or 0) if tracker_available else None
        ftds = int(row.ftds or 0) if tracker_available else None
        confirmed = int(row.confirmed_deposits or 0) if tracker_available else None
        clicks = int(row.clicks) if row.clicks is not None else None
        spend = Decimal(row.spend) if row.spend is not None else None
        cpc = (
            str((spend / clicks).quantize(Decimal("0.0001")))
            if spend is not None and clicks
            else None
        )
        cpr = (
            str((spend / registrations).quantize(Decimal("0.01")))
            if spend is not None and registrations
            else None
        )
        active_action = None
        if row.active_task_id is not None:
            active_row = type(
                "ActiveTask",
                (),
                {
                    "id": row.active_task_id,
                    "task_type": row.active_task_type,
                    "status": row.active_task_status,
                    "payload": row.active_task_payload,
                    "result": row.active_task_result,
                    "requested_by": row.active_task_requested_by,
                    "last_error": row.active_task_last_error,
                    "created_at": row.active_task_created_at,
                    "updated_at": row.active_task_updated_at,
                    "correlation_id": row.active_task_correlation_id,
                    "target_label": row.ad_name,
                },
            )
            active_action = _task_item(active_row)
        result_rows.append(
            {
                "id": str(row.id),
                "fb_ad_id": str(row.fb_ad_id),
                "name": str(row.ad_name),
                "campaign_id": str(row.campaign_id),
                "campaign_name": str(row.campaign_name),
                "adset_id": str(row.adset_id),
                "adset_name": str(row.adset_name),
                "account_id": str(row.ad_account_id) if row.ad_account_id else None,
                "delivery_status": str(row.delivery_status) if row.delivery_status else None,
                "data_state": data_state,
                "severity": row_severity,
                "as_of": cycle_ts,
                "metrics": {
                    "spend": str(spend) if spend is not None else None,
                    "impressions": int(row.impressions) if row.impressions is not None else None,
                    "clicks": clicks,
                    "registrations": registrations,
                    "ftd": ftds,
                    "confirmed_deposits": confirmed,
                    "cpc": cpc,
                    "cost_per_registration": cpr,
                },
                "active_action": active_action,
            }
        )
    total = int(rows[0].total_count) if rows else 0
    quality_total = int(rows[0].quality_total) if rows else 0
    unavailable_count = int(rows[0].unavailable_count) if rows else 0
    stale_count = int(rows[0].stale_count) if rows else 0
    incomplete_count = int(rows[0].incomplete_count) if rows else 0
    degraded_count = unavailable_count + stale_count + incomplete_count
    if quality_total > 0 and unavailable_count == quality_total:
        row_state = "unavailable"
    elif quality_total > 0 and stale_count == quality_total:
        row_state = "stale"
    elif degraded_count > 0:
        row_state = "partial"
    else:
        row_state = "ready"
    return {
        "rows": result_rows,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
        "as_of": rows[0].section_as_of if rows else None,
        "row_state": row_state,
    }


__all__ = [
    "fetch_operator_actions",
    "fetch_operator_ads",
    "fetch_operator_events",
    "fetch_operator_incident",
    "fetch_operator_incident_page",
    "fetch_operator_incidents",
    "fetch_operator_revision",
    "fetch_operator_scan_state",
    "task_action_kind",
    "task_action_reason",
    "task_action_state",
]
