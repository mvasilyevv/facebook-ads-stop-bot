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

# M-7 (аудит 2026-07-12): верхняя граница catch-up после простоя. Если воркер лежал
# дольше lookback (особенно через полночь UTC), хвост прошлого дня не пересчитался бы
# при фикс-окне [now-2h, now]. Тянем окно до last_run_at из аудита, но не глубже
# MAX_CATCHUP (защита от разового пересчёта всей истории при первом запуске / потере
# аудита). Пересчёт идемпотентен (absolute recompute), лишние дни лишь замедляют прогон.
MAX_CATCHUP = timedelta(days=2)


async def _read_last_run_at(engine: AsyncEngine) -> datetime | None:
    """last_run_at из system_config.tracker_aggregator_runs (None если нет/битый)."""
    try:
        async with engine.connect() as conn:
            raw = (
                await conn.execute(
                    text(
                        "SELECT value->>'last_run_at' FROM system_config "
                        "WHERE key = 'tracker_aggregator_runs'"
                    )
                )
            ).scalar()
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception as exc:  # noqa: BLE001 — аудит недоступен → просто без catch-up
        logger.warning("tracker_aggregator: не удалось прочитать last_run_at: %s", exc)
        return None


async def run_once(
    engine: AsyncEngine,
    *,
    now: datetime | None = None,
    lookback: timedelta = DEFAULT_LOOKBACK,
) -> AggregationResult:
    """Один прогон агрегации. Пишет аудит в system_config (best-effort)."""
    now = now or datetime.now(timezone.utc)
    window_start = now - lookback

    # Catch-up: если с прошлого прогона прошло больше lookback (простой воркера),
    # тянем окно до last_run_at (не глубже MAX_CATCHUP) — иначе хвост дня простоя
    # никогда не пересчитается. Пересчёт целых дней идемпотентен.
    last_run = await _read_last_run_at(engine)
    if last_run is not None and last_run < window_start:
        catchup_floor = now - MAX_CATCHUP
        window_start = max(last_run, catchup_floor)
        logger.info(
            "tracker_aggregator: catch-up после простоя — окно расширено до %s (last_run=%s)",
            window_start,
            last_run,
        )

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
