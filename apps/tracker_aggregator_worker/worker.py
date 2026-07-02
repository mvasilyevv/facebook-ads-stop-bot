# -*- coding: utf-8 -*-
"""Логика одного прогона tracker_aggregator.

Один прогон = пересчёт tracker_aggregate за окно [now - lookback, now].
Окно с запасом (по умолчанию 2 часа) перекрывает интервал между прогонами и
корректно переживает полночь UTC (пересчитываются целые дни — см. aggregator).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.adset_pro.aggregator import AggregationResult, aggregate_postback_events

logger = logging.getLogger(__name__)

# Запас окна: между прогонами проходит ~интервал, но берём шире чтобы не терять
# события на границе и хватало на catch-up после простоя воркера.
DEFAULT_LOOKBACK = timedelta(hours=2)


async def run_once(
    engine: AsyncEngine,
    *,
    now: datetime | None = None,
    lookback: timedelta = DEFAULT_LOOKBACK,
) -> AggregationResult:
    """Один прогон агрегации. Пишет аудит в system_config (best-effort)."""
    now = now or datetime.now(timezone.utc)
    window_start = now - lookback
    logger.info("tracker_aggregator: прогон за окно [%s .. %s]", window_start, now)

    result = await aggregate_postback_events(
        engine,
        window_start=window_start,
        window_end=now,
    )

    try:
        await _write_audit(engine, result)
    except Exception as exc:  # noqa: BLE001 — аудит не должен ронять прогон
        logger.warning("tracker_aggregator: не удалось записать аудит: %s", exc)

    return result


async def _write_audit(engine: AsyncEngine, result: AggregationResult) -> None:
    """Сохраняет итог последнего прогона в system_config (ключ tracker_aggregator_runs)."""
    payload = {
        "last_run_at": result.window_end.isoformat(),
        "window_start": result.window_start.isoformat(),
        "day_floor": result.day_floor.isoformat(),
        "day_ceil": result.day_ceil.isoformat(),
        "rows_upserted": result.rows_upserted,
        "rows_inserted": result.rows_inserted,
        "rows_updated": result.rows_updated,
        "deposits_total": result.deposits_total,
        "revenue_total": str(result.revenue_total),
        # LOW (аудит 02.07): видимость молчаливого дропа невалидного country прямо в
        # аудит-снимке — раньше отследить можно было только по логам.
        "rows_dropped_invalid_country": result.rows_dropped_invalid_country,
    }
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (:k, CAST(:v AS JSONB), 'Аудит запусков tracker_aggregator_worker')
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """
            ),
            {"k": "tracker_aggregator_runs", "v": json.dumps(payload)},
        )
