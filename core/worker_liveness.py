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

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator, Final, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Верхняя граница на саму запись: это единственный await между claim'ом и
# началом работы на money-полосе (окно аренды может быть всего 30с), и он не
# смеет быть неограниченным. Отказ пула/сети должен проиграть по таймауту
# быстрее, чем истечёт аренда, а не повиснуть на TCP retransmit.
_WRITE_TIMEOUT_SECONDS: Final[float] = 3.0

# Generic-heartbeat каденция одинакова у всех воркеров (core/worker_metrics.py
# метрика тикает раз в 15с из metrics_loop).
HEARTBEAT_INTERVAL_SECONDS: Final[int] = 15

# Ожидаемый интервал (секунды), с которым воркер реально трогает БД в своём
# РАБОЧЕМ цикле — не generic Prometheus heartbeat раз в 15с, а именно опрос
# очереди/плановая проверка.
#
# Там, где сам воркер берёт каденцию из переменной окружения, порог обязан
# читать ТУ ЖЕ переменную с тем же дефолтом — иначе поднятый интервал сверки
# без единой правки кода даёт вечный ложный CRITICAL (review issue #176 Л2).
# Оба процесса (сам воркер и api, который считает порог для снимка) получают
# переменные из общего APP_ENV_FILE — deploy/compose/docker-compose.app.yml,
# `x-worker-common` и сервис `api` подключают один и тот же файл.
WORKER_POLL_INTERVAL_SECONDS: Final[Mapping[str, int]] = {
    "campaign_creator": 5,  # apps/campaign_creator_worker/main.py: IDLE_SLEEP_SECONDS (константа)
    "autopause": 1,  # apps/meta_api_worker/main.py: IDLE_SLEEP_SECONDS (константа)
    "meta_api": 1,  # apps/meta_api_worker/main.py: IDLE_SLEEP_SECONDS (константа)
    "cleanup": 900,  # apps/cleanup_worker/main.py: _STORAGE_CHECK_INTERVAL_SECONDS (константа)
    "digest_scheduler": int(os.environ.get("DIGEST_CHECK_INTERVAL_SEC", "60")),
    "health_watchdog": int(os.environ.get("HEALTH_WATCHDOG_INTERVAL_SEC", "60")),
    "observer": 15,  # apps/observer_worker/main.py: metrics_loop interval (константа)
    "reconciler": int(os.environ.get("RECONCILER_INTERVAL_SEC", "30")),
    # Отметка теперь троттлится в самом воркере до HEARTBEAT_INTERVAL_SECONDS
    # независимо от скорости горячего цикла (review issue #176 Л1).
    "telegram_delivery": HEARTBEAT_INTERVAL_SECONDS,
    "telegram_updates": HEARTBEAT_INTERVAL_SECONDS,
    "tracker_reconciliation_worker": int(
        float(os.environ.get("TRACKER_EVENT_DB_POLL_SECONDS", "1"))
    ),
}

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

    Always >= ``heartbeat_stale_after_seconds()`` by construction. When
    ``poll_success=True`` last wrote both columns simultaneously (the normal
    case), a fully dead process lets both ages grow from the same instant. A
    smaller poll threshold would then cross first and misreport a dead
    process as merely "stalled" (still responding, just not polling) for the
    gap between the two thresholds — exactly the wrong diagnosis in the first
    seconds of the incident this module exists to catch.
    """
    interval = WORKER_POLL_INTERVAL_SECONDS.get(worker_name, HEARTBEAT_INTERVAL_SECONDS)
    return max(
        heartbeat_stale_after_seconds(),
        _MIN_STALE_AFTER_SECONDS,
        interval * _GRACE_MULTIPLIER,
    )


async def _write_heartbeat_row(
    engine: AsyncEngine, worker_name: str, *, poll_success: bool
) -> None:
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


async def record_worker_heartbeat(
    engine: AsyncEngine,
    worker_name: str,
    *,
    poll_success: bool = False,
) -> None:
    """Upsert the durable liveness row for ``worker_name``.

    This is a best-effort secondary signal: nothing here may ever crash the
    caller's real work loop. A production run against real asyncpg proved a
    narrow ``except SQLAlchemyError`` insufficient — a refused connection
    (``ConnectionRefusedError``), a DNS blip (``socket.gaierror``) or a pool
    timeout (``TimeoutError``, unified with ``asyncio.TimeoutError`` since
    3.11) can all surface unwrapped, below SQLAlchemy's own exception
    hierarchy, from ``engine.begin()`` establishing a *new* connection. A
    liveness write is not business logic: catching every ``Exception`` here
    is the correct, narrow scope for this one function, not a general worker
    loop turning failure into an infinite silent retry. ``asyncio.CancelledError``
    is a ``BaseException`` and is deliberately never caught — it is how a
    worker's own deadline/shutdown reaches this call, and must keep
    propagating.

    Callers on the money lane additionally wrap this call at the call site
    (defense in depth): if a future change to this function ever narrows the
    catch again, a single call site cannot take the whole worker process down
    with it.
    """
    try:
        await asyncio.wait_for(
            _write_heartbeat_row(engine, worker_name, poll_success=poll_success),
            timeout=_WRITE_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 — best-effort liveness write, see docstring
        logger.warning("worker heartbeat persist failed for %s", worker_name, exc_info=True)


@asynccontextmanager
async def poll_heartbeat_while_running(
    engine: AsyncEngine,
    worker_name: str,
    *,
    interval_seconds: float | None = None,
) -> AsyncIterator[None]:
    """Keep ``last_poll_success_at`` fresh while one task actively executes.

    A claim-time ``poll_success=True`` mark proves the queue was touched once,
    but a claimed task can then run for minutes (video upload, slow Meta
    processing) — long enough to cross ``poll_stale_after_seconds`` even
    though the worker is doing exactly its job. This periodic tick is a plain,
    independent side task: it never touches lease/fencing/control-plane state
    and its own failures cannot escape (``record_worker_heartbeat`` already
    swallows everything but ``CancelledError``), so it cannot affect the task
    it runs alongside.

    ``interval_seconds=None`` означает «каденция всех воркеров», и берётся она
    из ``HEARTBEAT_INTERVAL_SECONDS`` в момент вызова, а не в момент импорта.
    Значение по умолчанию, вычисленное при определении функции, — вторая копия
    той же константы: она перестаёт совпадать с ней молча, и никакой вызов
    этого не показывает.
    """
    interval = HEARTBEAT_INTERVAL_SECONDS if interval_seconds is None else interval_seconds

    async def _tick() -> None:
        while True:
            await asyncio.sleep(interval)
            await record_worker_heartbeat(engine, worker_name, poll_success=True)

    ticker = asyncio.create_task(_tick())
    try:
        yield
    finally:
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "WORKER_POLL_INTERVAL_SECONDS",
    "heartbeat_stale_after_seconds",
    "poll_heartbeat_while_running",
    "poll_stale_after_seconds",
    "record_worker_heartbeat",
]
