# -*- coding: utf-8 -*-
"""Durable PostgreSQL liveness for the eleven background workers (issue #176).

``core/worker_metrics.py`` exports a process-local Prometheus gauge: it proves
a process is alive to whoever scrapes it, but Prometheus is an acceleration
layer, not the source of truth, and the operator snapshot must read state
from PostgreSQL. Until this module existed, a worker's heartbeat never
reached PostgreSQL at all — 18.08.2026 the campaign creation worker stopped
draining its queue for eleven hours and nothing on the operator screen
changed, because the only liveness signal lived in a metrics endpoint no
snapshot query ever touched.

Two durable signals per worker, not one:

- ``last_heartbeat_at`` — the worker's liveness loop is still ticking. Most
  workers update this from a small coroutine every ~15 seconds, independent
  of whether there is any work to do.
- ``last_poll_success_at`` — the worker's REAL work loop (the one that claims
  a task or runs its scheduled check) completed an iteration, whether or not
  it found anything to act on. An idle worker with an empty queue keeps
  advancing this and must look healthy. A worker whose work loop hangs on an
  external call stops advancing it — while, on several workers, a *separate*
  heartbeat coroutine keeps ticking regardless, because the two loops are
  independent tasks. That gap is exactly what hid the 18.08 incident: the
  process was not dead, only its queue-claiming loop was stuck.

Callers mark heartbeat-only ticks with ``poll_success=False`` and mark a
completed work-loop iteration with ``poll_success=True``; the latter always
advances both columns, since a successful poll is also proof the process is
alive.
"""

from __future__ import annotations

import logging
from typing import Final, Mapping

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Ожидаемый интервал (секунды), с которым воркер реально трогает БД в своём
# РАБОЧЕМ цикле — не generic Prometheus heartbeat раз в 15с, а именно опрос
# очереди/плановая проверка. Источники значений — константы соответствующих
# apps/*_worker/main.py (IDLE_SLEEP_SECONDS, *_INTERVAL_SEC и т.п.).
WORKER_POLL_INTERVAL_SECONDS: Final[Mapping[str, int]] = {
    "campaign_creator": 5,
    "autopause": 1,
    "meta_api": 1,
    "cleanup": 900,
    "digest_scheduler": 60,
    "health_watchdog": 60,
    "observer": 15,
    "reconciler": 30,
    "telegram_delivery": 2,
    "telegram_updates": 2,
    "tracker_reconciliation_worker": 1,
}

# Generic-heartbeat каденция одинакова у всех воркеров (core/worker_metrics.py
# метрика тикает раз в 15с из metrics_loop).
HEARTBEAT_INTERVAL_SECONDS: Final[int] = 15

# Запас перед тем, как считать сигнал устаревшим: сеть/GC-паузы не должны
# превращаться в ложный CRITICAL при первом же пропущенном тике.
_GRACE_MULTIPLIER: Final[int] = 5
_MIN_STALE_AFTER_SECONDS: Final[int] = 60


def heartbeat_stale_after_seconds() -> int:
    """Grace window for the process-alive signal, shared by every worker."""
    return max(_MIN_STALE_AFTER_SECONDS, HEARTBEAT_INTERVAL_SECONDS * _GRACE_MULTIPLIER)


def poll_stale_after_seconds(worker_name: str) -> int:
    """Grace window for the real work-loop signal, scaled to its own cadence.

    Unregistered worker names still get a positive, finite grace window
    (falling back to the generic heartbeat cadence) instead of raising, so a
    typo in a caller cannot turn a monitoring signal into a crash.
    """
    interval = WORKER_POLL_INTERVAL_SECONDS.get(worker_name, HEARTBEAT_INTERVAL_SECONDS)
    return max(_MIN_STALE_AFTER_SECONDS, interval * _GRACE_MULTIPLIER)


async def record_worker_heartbeat(
    engine: AsyncEngine,
    worker_name: str,
    *,
    poll_success: bool = False,
) -> None:
    """Upsert the durable liveness row for ``worker_name``.

    This is a best-effort secondary signal: a transient PostgreSQL failure
    here must never crash the caller's real work loop (which is already
    talking to the same database for its actual job and will surface that
    failure through its own path). ``SQLAlchemyError`` is caught narrowly and
    logged; anything else propagates.
    """
    try:
        async with engine.begin() as conn:
            if poll_success:
                await conn.execute(
                    text(
                        """
                        INSERT INTO worker_heartbeats
                            (worker_name, last_heartbeat_at, last_poll_success_at)
                        VALUES (:worker_name, NOW(), NOW())
                        ON CONFLICT (worker_name) DO UPDATE SET
                            last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                            last_poll_success_at = EXCLUDED.last_poll_success_at
                        """
                    ),
                    {"worker_name": worker_name},
                )
            else:
                await conn.execute(
                    text(
                        """
                        INSERT INTO worker_heartbeats (worker_name, last_heartbeat_at)
                        VALUES (:worker_name, NOW())
                        ON CONFLICT (worker_name) DO UPDATE SET
                            last_heartbeat_at = EXCLUDED.last_heartbeat_at
                        """
                    ),
                    {"worker_name": worker_name},
                )
    except SQLAlchemyError:
        logger.warning("worker heartbeat persist failed for %s", worker_name, exc_info=True)


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "WORKER_POLL_INTERVAL_SECONDS",
    "heartbeat_stale_after_seconds",
    "poll_stale_after_seconds",
    "record_worker_heartbeat",
]
