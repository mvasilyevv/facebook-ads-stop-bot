# -*- coding: utf-8 -*-
"""Dashboard endpoints: AdsPage / DashboardPage композитные view.

Endpoints (с prefix /api от auto-discovery):
    GET /dashboard/ads        — список ad'ов с alert_state, метриками, offer.
    GET /dashboard/alerts     — append-only лог FSM-событий с JOIN'ами по ad.
    GET /dashboard/incidents  — активные инциденты (warning_sent/stop_sent).

Все endpoint'ы:
- Используют партиционный WHERE по cycle_ts/created_at (partition pruning).
- limit cap'нут на 500 (защита от мегабайтных JSON'ов).
- В заголовке `X-Total-Count` /dashboard/ads пишет реальный COUNT с теми же
  фильтрами (без LIMIT) — для пагинации фронта.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import text

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.dashboard import (
    AdSnapshotOut,
    AlertEventOut,
    IncidentOut,
)
from apps.api.utils.alert_serializer import alert_event_row_to_out
from apps.api.utils.partition import default_window
from core.dashboard.snapshot import build_ad_snapshot, build_incidents_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

# Жёсткие лимиты для всех dashboard endpoint'ов
_MAX_LIMIT = 500
_DEFAULT_ADS_LIMIT = 200
_DEFAULT_ALERTS_LIMIT = 100
_DEFAULT_INCIDENTS_LIMIT = 100

# Допустимые значения alert_state (для фильтра)
_VALID_ALERT_STATES = {"normal", "warning_sent", "stop_sent", "claimed", "disabled"}
_VALID_STAGES = {"warning", "stop"}


def _parse_csv(value: str | None) -> list[str] | None:
    """Разбирает CSV-строку в список. Пустой/None → None."""
    if not value:
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return parts or None


# ─────────────────────── GET /dashboard/ads ──────────────────────────────────


@router.get("/dashboard/ads", response_model=list[AdSnapshotOut])
async def list_ad_snapshots(
    engine: DepEngine,
    response: Response,
    alert_state: str | None = Query(
        default=None,
        description="CSV alert_state'ов (warning_sent,stop_sent,...)",
    ),
    fb_ad_ids: str | None = Query(default=None, description="CSV Meta-ID объявлений"),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=_DEFAULT_ADS_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """Список ad'ов с alert_state, последней метрикой и offer.

    Партиционный фильтр по ad_metrics.cycle_ts применяется внутри LATERAL'а
    (последние 7 дней). Если за окно нет метрик — metrics=None.

    X-Total-Count в headers — реальный COUNT с теми же фильтрами без LIMIT,
    для пагинации фронта.
    """
    alert_states = _parse_csv(alert_state)
    if alert_states:
        bad = [s for s in alert_states if s not in _VALID_ALERT_STATES]
        if bad:
            raise HTTPException(
                status_code=422,
                detail=f"Неизвестные alert_state: {bad}",
            )

    fb_ad_ids_list = _parse_csv(fb_ad_ids)

    snapshots = await build_ad_snapshot(
        engine,
        fb_ad_ids=fb_ad_ids_list,
        alert_states=alert_states,
        limit=limit,
        offset=offset,
        include_inactive=include_inactive,
    )

    # X-Total-Count — отдельный COUNT с теми же фильтрами, без LIMIT/OFFSET.
    total = await _count_ads(
        engine,
        fb_ad_ids=fb_ad_ids_list,
        alert_states=alert_states,
        include_inactive=include_inactive,
    )
    response.headers["X-Total-Count"] = str(total)

    return snapshots


async def _count_ads(
    engine,
    *,
    fb_ad_ids: list[str] | None,
    alert_states: list[str] | None,
    include_inactive: bool,
) -> int:
    """COUNT(*) с теми же фильтрами что и /dashboard/ads, без LIMIT/OFFSET."""
    where_clauses: list[str] = []
    params: dict[str, Any] = {}

    if not include_inactive:
        where_clauses.append("fb_ads.is_active = true")
    if fb_ad_ids:
        where_clauses.append("fb_ads.fb_ad_id = ANY(:fb_ad_ids)")
        params["fb_ad_ids"] = list(fb_ad_ids)
    if alert_states:
        where_clauses.append("COALESCE(s.alert_state, 'normal') = ANY(:alert_states)")
        params["alert_states"] = list(alert_states)

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"
    sql = f"""
        SELECT COUNT(*) AS cnt
        FROM fb_ads
        LEFT JOIN ad_alert_state s ON s.ad_id = fb_ads.id
        WHERE {where_sql}
    """
    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params)
        row = result.one()
        return int(row.cnt)


# ─────────────────────── GET /dashboard/alerts ───────────────────────────────


@router.get("/dashboard/alerts", response_model=list[AlertEventOut])
async def list_alert_events(
    engine: DepEngine,
    stage: str | None = Query(default=None, description="warning | stop"),
    fb_ad_id: str | None = Query(default=None, description="Meta-ID объявления"),
    from_iso: str | None = Query(default=None, description="ISO-8601 начало окна"),
    to_iso: str | None = Query(default=None, description="ISO-8601 конец окна"),
    limit: int = Query(default=_DEFAULT_ALERTS_LIMIT, ge=1, le=_MAX_LIMIT),
) -> list[dict[str, Any]]:
    """Append-only лог FSM-событий с JOIN'ами по fb_ads/fb_campaigns/offers.

    Партиционный фильтр по alert_events.created_at — обязательный.
    Если from_iso/to_iso не переданы — дефолт last 24h.
    Если from_iso > to_iso → 422.

    CRITICAL: имена полей AlertEvent — `stage` / `matched_rule_codes`
    (не `event_type` / `rule_codes`). triggered_by_rule_codes не существует
    в ORM — возвращаем None в ответе для совместимости с frontend shape.
    """
    # Фронт исторически шлёт stage в UPPERCASE (WARNING/STOP), v2-схема хранит lowercase.
    if stage is not None:
        stage = stage.lower()
        if stage not in _VALID_STAGES:
            raise HTTPException(
                status_code=422,
                detail=f"stage должен быть один из: {sorted(_VALID_STAGES)}",
            )

    # Временное окно — partition pruning.
    if from_iso or to_iso:
        try:
            from_dt = datetime.fromisoformat(from_iso) if from_iso else default_window(hours=24)[0]
            to_dt = datetime.fromisoformat(to_iso) if to_iso else datetime.now(UTC)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Неверный формат даты: {exc}") from exc
    else:
        from_dt, to_dt = default_window(hours=24)

    if from_dt > to_dt:
        raise HTTPException(
            status_code=422,
            detail="from_iso не может быть больше to_iso",
        )

    where_clauses: list[str] = [
        "ae.created_at >= :from_dt",
        "ae.created_at <= :to_dt",
    ]
    params: dict[str, Any] = {
        "from_dt": from_dt,
        "to_dt": to_dt,
        "limit": min(limit, _MAX_LIMIT),
    }
    if stage:
        where_clauses.append("ae.stage = :stage")
        params["stage"] = stage
    if fb_ad_id:
        where_clauses.append("fb_ads.fb_ad_id = :fb_ad_id")
        params["fb_ad_id"] = fb_ad_id

    where_sql = " AND ".join(where_clauses)

    sql = f"""
        SELECT
            ae.id              AS id,
            ae.ad_id           AS ad_internal_id,
            ae.stage           AS stage,
            ae.matched_rule_codes AS matched_rule_codes,
            ae.metrics_json    AS metrics_json,
            ae.created_at      AS created_at,
            fb_ads.fb_ad_id    AS fb_ad_id,
            fb_ads.ad_name     AS ad_name,
            fb_campaigns.campaign_name AS campaign_name,
            offers.code        AS offer_code
        FROM alert_events ae
        LEFT JOIN fb_ads      ON fb_ads.id = ae.ad_id
        LEFT JOIN fb_adsets   ON fb_adsets.id = fb_ads.adset_id
        LEFT JOIN fb_campaigns ON fb_campaigns.id = fb_adsets.campaign_id
        LEFT JOIN offers      ON offers.id = fb_campaigns.offer_id
        WHERE {where_sql}
        ORDER BY ae.created_at DESC
        LIMIT :limit
    """

    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params)
        rows = result.all()

    return [alert_event_row_to_out(r) for r in rows]


# ─────────────────────── GET /dashboard/incidents ────────────────────────────


@router.get("/dashboard/incidents", response_model=list[IncidentOut])
async def list_incidents(
    engine: DepEngine,
    stage: str = Query(default="all", description="warning | stop | all"),
    limit: int = Query(default=_DEFAULT_INCIDENTS_LIMIT, ge=1, le=_MAX_LIMIT),
) -> list[dict[str, Any]]:
    """Активные инциденты: ad_alert_state IN ('warning_sent','stop_sent')
    AND (snoozed_until IS NULL OR snoozed_until < NOW()).

    Дополнительно к /dashboard/ads:
    - incident_open_since: last_transition_at (момент входа в текущее состояние).
    - incident_duration_seconds: NOW - incident_open_since.
    - transitions_count: COUNT(alert_events) от incident_open_since до NOW().

    transitions_count считается batch'ем за один запрос — никаких N+1.
    """
    # Фронт может прислать UPPERCASE — normalize в lowercase.
    stage = stage.lower()
    if stage not in {"warning", "stop", "all"}:
        raise HTTPException(
            status_code=422,
            detail="stage должен быть один из: warning, stop, all",
        )

    incidents = await build_incidents_snapshot(
        engine,
        stage=stage,
        limit=limit,
    )
    return incidents


__all__ = ["router"]
