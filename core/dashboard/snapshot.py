# -*- coding: utf-8 -*-
"""Composite-view: объединение fb_ads + alert_state + последняя метрика + оффер.

Используется тремя endpoint'ами дашборда: /dashboard/ads, /dashboard/incidents
(и косвенно /dashboard/alerts через JOIN).

Решения, которые важны:
- LATERAL подзапрос для последней метрики из ad_metrics (partitioned) включает
  обязательный фильтр `cycle_ts >= now() - INTERVAL '7 days'` — для partition
  pruning. Без него запрос становится full-scan по всем партициям.
- Если метрик за 7 дней нет — LEFT JOIN LATERAL даёт NULL'ы и `metrics: None`
  в результате.
- ad_alert_state может отсутствовать у нового ad → `alert_state: "normal"`.
- meta_api_observation LEFT JOIN — опциональный, NULL вернётся в meta_ad_status.
- Decimal сериализуется как str (Pydantic v2 + JSON friendly формат).
- delivery_status в схеме ad_metrics отсутствует — возвращаем None для
  совместимости с frontend (там поле опциональное).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Жёсткие лимиты на ответ, чтобы фронт не получил многомегабайтных JSON'ов.
_MAX_LIMIT = 500
# Партиционное окно для LATERAL'а — последние 7 суток.
_METRICS_LOOKBACK_DAYS = 7


# Маппинг полей метрик из строки ROW в dict ответа.
# (имя_в_ответе, имя_колонки_в_ad_metrics, признак_decimal)
_METRIC_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("spend", "m_spend", True),
    ("impressions", "m_impressions", False),
    ("clicks", "m_clicks", False),
    ("ctr", "m_ctr", True),
    ("cpc", "m_cpc", True),
    ("cpm", "m_cpm", True),
    ("reach", "m_reach", False),
    ("frequency", "m_frequency", True),
    ("leads", "m_leads", False),
    ("cost_per_lead", "m_cost_per_lead", True),
    ("registrations", "m_registrations", False),
    ("cost_per_registration", "m_cost_per_registration", True),
    ("deposits", "m_deposits", False),
)


def _decimal_to_str(value: Decimal | None) -> str | None:
    """Конвертирует Decimal → str, None → None.

    JSON-сериализаторы Pydantic для Decimal либо роняют float точность,
    либо требуют отдельной настройки. str — простой и стабильный путь.
    """
    if value is None:
        return None
    return str(value)


def _iso_or_none(value: Any) -> str | None:
    """ISO-формат для datetime или None."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _build_metrics_dict(row: Any) -> dict[str, Any] | None:
    """Собирает блок metrics из одной строки SELECT'а.

    Возвращает None, если для ad'а вообще нет последней метрики (m_cycle_ts IS NULL).
    Decimal-поля кодируются как str.
    """
    cycle_ts = getattr(row, "m_cycle_ts", None)
    if cycle_ts is None:
        return None

    out: dict[str, Any] = {"cycle_ts": _iso_or_none(cycle_ts)}
    for out_name, col, is_decimal in _METRIC_FIELDS:
        val = getattr(row, col, None)
        if is_decimal:
            out[out_name] = _decimal_to_str(val)
        else:
            out[out_name] = int(val) if val is not None else None
    return out


def _parse_rule_codes(raw: Any) -> list[str]:
    """Парсит matched_rule_codes из LATERAL'а в list[str].

    asyncpg возвращает JSONB-массивы как Python-list напрямую.
    Защита: если вдруг строка (json-encoded) — парсим вручную.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(c) for c in raw]
    # Если asyncpg вернул строку (нестандартный codec) — пробуем JSON.
    import json

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(c) for c in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _build_row_dict(row: Any) -> dict[str, Any]:
    """Преобразует одну строку SELECT'а в плоский dict для фронта."""
    metrics = _build_metrics_dict(row)

    # Разбираем коды правил из последнего AlertEvent (LATERAL last_ev).
    # last_ev_stage: 'stop' → stop_rule_codes, 'warning' → warning_rule_codes.
    last_ev_stage: str | None = getattr(row, "last_ev_stage", None)
    last_ev_codes = _parse_rule_codes(getattr(row, "last_ev_matched_rule_codes", None))
    stop_rule_codes: list[str] = []
    warning_rule_codes: list[str] = []
    if last_ev_stage == "stop":
        stop_rule_codes = last_ev_codes
    elif last_ev_stage == "warning":
        warning_rule_codes = last_ev_codes

    return {
        "fb_ad_id": row.fb_ad_id,
        "internal_id": str(row.internal_id),
        "ad_name": row.ad_name,
        "campaign_name": row.campaign_name,
        "adset_name": row.adset_name,
        "offer_code": row.offer_code,
        "offer_id": str(row.offer_id) if row.offer_id is not None else None,
        # ad_alert_state может отсутствовать — coalesce в SQL даёт 'normal'
        "alert_state": row.alert_state or "normal",
        "snoozed_until": _iso_or_none(row.snoozed_until),
        "open_state_token": (
            str(row.open_state_token) if row.open_state_token is not None else None
        ),
        "last_warning_at": _iso_or_none(row.last_warning_at),
        "last_stop_at": _iso_or_none(row.last_stop_at),
        "is_active": bool(row.is_active),
        "last_seen_at": _iso_or_none(row.last_seen_at),
        # delivery_status в схеме ad_metrics отсутствует — null для frontend-shape.
        "delivery_status": None,
        "meta_ad_status": row.meta_ad_status,
        # Коды сработавших правил из последнего AlertEvent.
        "stop_rule_codes": stop_rule_codes,
        "warning_rule_codes": warning_rule_codes,
        "metrics": metrics,
    }


def _build_sql(
    *,
    fb_ad_ids: list[str] | None,
    alert_states: list[str] | None,
    include_inactive: bool,
    incidents_only: bool,
    incident_stage: str | None,
    limit: int,
    offset: int,
) -> tuple[str, dict[str, Any]]:
    """Собирает SQL + bind-параметры. Не выполняет.

    Параметры:
        incidents_only: дополнительный фильтр для /dashboard/incidents:
            alert_state IN ('warning_sent','stop_sent') AND not_snoozed.
        incident_stage: 'warning' | 'stop' | None — сужает по AdAlertState.alert_state.

    Returns:
        Кортеж (sql_text, params_dict).
    """
    where_clauses: list[str] = []
    params: dict[str, Any] = {
        "limit": min(limit, _MAX_LIMIT),
        "offset": max(offset, 0),
        "lookback_days": _METRICS_LOOKBACK_DAYS,
    }

    if not include_inactive:
        where_clauses.append("fb_ads.is_active = true")

    if fb_ad_ids:
        where_clauses.append("fb_ads.fb_ad_id = ANY(:fb_ad_ids)")
        params["fb_ad_ids"] = list(fb_ad_ids)

    if alert_states:
        where_clauses.append("COALESCE(s.alert_state, 'normal') = ANY(:alert_states)")
        params["alert_states"] = list(alert_states)

    if incidents_only:
        # Активные инциденты: warning_sent / stop_sent, без активного snooze.
        where_clauses.append("s.alert_state IN ('warning_sent','stop_sent')")
        where_clauses.append("(s.snoozed_until IS NULL OR s.snoozed_until < NOW())")
        if incident_stage == "warning":
            where_clauses.append("s.alert_state = 'warning_sent'")
        elif incident_stage == "stop":
            where_clauses.append("s.alert_state = 'stop_sent'")
        # incident_stage == 'all' | None — без доп. условий

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    sql = f"""
    SELECT
        fb_ads.fb_ad_id                    AS fb_ad_id,
        fb_ads.id                          AS internal_id,
        fb_ads.ad_name                     AS ad_name,
        fb_campaigns.campaign_name         AS campaign_name,
        fb_adsets.adset_name               AS adset_name,
        offers.code                        AS offer_code,
        offers.id                          AS offer_id,
        s.alert_state                      AS alert_state,
        s.snoozed_until                    AS snoozed_until,
        s.open_state_token                 AS open_state_token,
        s.last_transition_at               AS last_transition_at,
        -- last_warning_at / last_stop_at в ad_alert_state отсутствуют как
        -- отдельные колонки; восстанавливаем из last_transition_at в зависимости
        -- от current_stage. NULL если соответствующая стадия не активна.
        CASE WHEN s.current_stage = 'warning' THEN s.last_transition_at END
            AS last_warning_at,
        CASE WHEN s.current_stage = 'stop'    THEN s.last_transition_at END
            AS last_stop_at,
        fb_ads.is_active                   AS is_active,
        fb_ads.last_seen_at                AS last_seen_at,
        mo.meta_ad_status                  AS meta_ad_status,
        -- LATERAL: последняя метрика за окно lookback_days
        latest_m.cycle_ts                  AS m_cycle_ts,
        latest_m.spend                     AS m_spend,
        latest_m.impressions               AS m_impressions,
        latest_m.clicks                    AS m_clicks,
        latest_m.ctr                       AS m_ctr,
        latest_m.cpc                       AS m_cpc,
        latest_m.cpm                       AS m_cpm,
        latest_m.reach                     AS m_reach,
        latest_m.frequency                 AS m_frequency,
        latest_m.leads                     AS m_leads,
        latest_m.cost_per_lead             AS m_cost_per_lead,
        latest_m.registrations             AS m_registrations,
        latest_m.cost_per_registration     AS m_cost_per_registration,
        latest_m.deposits                  AS m_deposits,
        -- LATERAL: последний AlertEvent за окно lookback_days — для rule_codes.
        -- Partition pruning: фильтр по created_at обязателен.
        last_ev.matched_rule_codes         AS last_ev_matched_rule_codes,
        last_ev.stage                      AS last_ev_stage
    FROM fb_ads
    LEFT JOIN ad_alert_state s     ON s.ad_id = fb_ads.id
    LEFT JOIN fb_adsets            ON fb_ads.adset_id = fb_adsets.id
    LEFT JOIN fb_campaigns         ON fb_adsets.campaign_id = fb_campaigns.id
    LEFT JOIN offers               ON fb_campaigns.offer_id = offers.id
    LEFT JOIN meta_api_observation mo ON mo.ad_id = fb_ads.id
    LEFT JOIN LATERAL (
        SELECT cycle_ts, spend, impressions, clicks, ctr, cpc, cpm, reach,
               frequency, leads, cost_per_lead, registrations,
               cost_per_registration, deposits
        FROM ad_metrics
        WHERE ad_metrics.ad_id = fb_ads.id
          AND ad_metrics.cycle_ts >= NOW() - make_interval(days => :lookback_days)
        ORDER BY cycle_ts DESC
        LIMIT 1
    ) latest_m ON true
    LEFT JOIN LATERAL (
        SELECT ae.matched_rule_codes, ae.stage
        FROM alert_events ae
        WHERE ae.ad_id = fb_ads.id
          AND ae.created_at >= NOW() - make_interval(days => :lookback_days)
        ORDER BY ae.created_at DESC
        LIMIT 1
    ) last_ev ON true
    WHERE {where_sql}
    ORDER BY fb_ads.last_seen_at DESC NULLS LAST, fb_ads.id ASC
    LIMIT :limit OFFSET :offset
    """
    return sql, params


async def build_ad_snapshot(
    engine: AsyncEngine,
    *,
    fb_ad_ids: list[str] | None = None,
    alert_states: list[str] | None = None,
    limit: int = 200,
    offset: int = 0,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """Композитный snapshot ad'ов для AdsPage/IncidentsPage.

    JOIN: fb_ads + ad_alert_state + LATERAL(latest ad_metrics) + fb_adsets +
    fb_campaigns + offers + meta_api_observation. Все JOIN'ы — LEFT, чтобы
    отсутствие данных не отфильтровывало ad из ответа.

    LATERAL для ad_metrics несёт обязательный partition-фильтр
    `cycle_ts >= NOW() - INTERVAL ':lookback_days days'` — без него Postgres
    делает full-scan по всем партициям.

    Args:
        engine: AsyncEngine SQLAlchemy.
        fb_ad_ids: фильтр по Meta-ID объявлений (список строк).
        alert_states: фильтр по FSM-состоянию (normal/warning_sent/stop_sent/
            claimed/disabled).
        limit: до _MAX_LIMIT (500), всё что больше — обрезается.
        offset: для пагинации.
        include_inactive: если False — только is_active=true.

    Returns:
        Список плоских dict'ов, по одному на ad. Decimal сериализуется в str.
    """
    sql, params = _build_sql(
        fb_ad_ids=fb_ad_ids,
        alert_states=alert_states,
        include_inactive=include_inactive,
        incidents_only=False,
        incident_stage=None,
        limit=limit,
        offset=offset,
    )

    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params)
        rows = result.all()

    return [_build_row_dict(r) for r in rows]


async def build_incidents_snapshot(
    engine: AsyncEngine,
    *,
    stage: str = "all",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Snapshot активных инцидентов (warning_sent/stop_sent, не snoozed).

    Дополнительно к build_ad_snapshot:
    - incident_open_since: last_transition_at из ad_alert_state.
    - incident_duration_seconds: NOW - last_transition_at.
    - transitions_count: COUNT(alert_events WHERE ad_id=... AND created_at >=
      incident_open_since).

    transitions_count считается отдельным запросом батчем для всех ad'ов в
    результате — N+1 защита.

    Args:
        engine: AsyncEngine.
        stage: 'warning' | 'stop' | 'all'.
        limit: до _MAX_LIMIT.

    Returns:
        Список dict'ов с полями build_ad_snapshot + incident_* поля.
    """
    incident_stage: str | None = stage if stage in {"warning", "stop"} else None
    sql, params = _build_sql(
        fb_ad_ids=None,
        alert_states=None,
        include_inactive=False,
        incidents_only=True,
        incident_stage=incident_stage,
        limit=limit,
        offset=0,
    )

    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params)
        rows = result.all()

    if not rows:
        return []

    # Собираем internal_id → last_transition_at для batch-COUNT по alert_events.
    starts_by_id: dict[str, Any] = {str(r.internal_id): r.last_transition_at for r in rows}
    transitions_by_id: dict[str, int] = {k: 0 for k in starts_by_id}

    # Считаем трансиции одним запросом: для каждого ad'а — COUNT(*) с
    # WHERE ad_id=... AND created_at >= его собственный last_transition_at.
    # Реализуем через UNNEST(arrays) + JOIN с alert_events.
    ad_ids_list = [r.internal_id for r in rows if r.last_transition_at is not None]
    starts_list = [r.last_transition_at for r in rows if r.last_transition_at is not None]

    if ad_ids_list:
        # Partition pruning: добавляем глобальный нижний фильтр по min(starts).
        min_start = min(starts_list)
        async with engine.connect() as conn:
            cnt_result = await conn.execute(
                text(
                    """
                    SELECT pairs.ad_id AS ad_id, COUNT(ae.id) AS cnt
                    FROM unnest(
                        CAST(:ad_ids AS uuid[]),
                        CAST(:starts AS timestamptz[])
                    ) AS pairs(ad_id, start_at)
                    LEFT JOIN alert_events ae
                        ON ae.ad_id = pairs.ad_id
                        AND ae.created_at >= pairs.start_at
                        AND ae.created_at >= :min_start
                    GROUP BY pairs.ad_id
                    """
                ),
                {
                    "ad_ids": ad_ids_list,
                    "starts": starts_list,
                    "min_start": min_start,
                },
            )
            for row in cnt_result.all():
                transitions_by_id[str(row.ad_id)] = int(row.cnt)

    # Сборка результата.
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for r in rows:
        base = _build_row_dict(r)
        open_since = starts_by_id[str(r.internal_id)]
        duration_seconds: int | None = None
        if open_since is not None:
            # SQLAlchemy возвращает aware-datetime, разность в секундах.
            try:
                duration_seconds = max(int((now - open_since).total_seconds()), 0)
            except TypeError:
                # Если start вдруг naive — приводим консервативно.
                duration_seconds = None
        base["incident_open_since"] = _iso_or_none(open_since)
        base["incident_duration_seconds"] = duration_seconds
        base["transitions_count"] = transitions_by_id.get(str(r.internal_id), 0)
        out.append(base)

    return out
