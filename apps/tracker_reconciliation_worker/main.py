# -*- coding: utf-8 -*-
"""Durable Tracker event processing plus provider-inbox reconciliation."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import UTC, datetime

import redis.asyncio as redis_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.adset_pro.processing import (
    TrackerLeaseLostError,
    claim_event_tasks,
    mark_task_retry,
    process_event_task,
)
from core.adset_pro.reconciliation import reconcile_provider_events
from core.db import WORKER_ENGINE_KWARGS
from core.metrics import (
    TRACKER_EVENT_BACKLOG,
    TRACKER_PROCESSING_LATENCY,
    TRACKER_UNMATCHED_EVENTS,
)
from core.observer.scan_tasks import enqueue_observer_scan, observer_scan_idempotency_key
from core.pubsub import CHANNEL_TRACKER_WAKEUP
from core.safe_diagnostics import safe_exception_diagnostic
from core.worker_metrics import mark_worker_heartbeat

logger = logging.getLogger("tracker_reconciliation_worker")

WORKER_NAME = "tracker_reconciliation_worker"
_METRICS_INTERVAL_SECONDS = 15.0

_RECONCILIATION_INTERVAL_SECONDS = int(
    os.environ.get("TRACKER_RECONCILIATION_INTERVAL_SECONDS", "300")
)
_DB_POLL_SECONDS = float(os.environ.get("TRACKER_EVENT_DB_POLL_SECONDS", "1"))


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


async def metrics_loop(stop: asyncio.Event) -> None:
    """Refresh process-local Prometheus liveness; Redis remains wakeup-only."""
    while not stop.is_set():
        mark_worker_heartbeat(WORKER_NAME)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_METRICS_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def wakeup_listener(redis_client, stop: asyncio.Event, wakeup: asyncio.Event) -> None:
    """Best-effort Redis wakeup; DB polling remains authoritative."""
    pubsub = redis_client.pubsub()
    try:
        await pubsub.subscribe(CHANNEL_TRACKER_WAKEUP)
        while not stop.is_set():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is not None:
                wakeup.set()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "tracker wakeup listener unavailable; using DB polling (%s)",
            safe_exception_diagnostic(exc),
        )
    finally:
        try:
            await pubsub.unsubscribe(CHANNEL_TRACKER_WAKEUP)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


async def drain_event_tasks(
    engine: AsyncEngine,
    *,
    limit: int = 100,
) -> int:
    """Drain one claimed batch and publish only after each DB commit."""
    claims = await claim_event_tasks(engine, limit=limit)
    for claim in claims:
        task_id = claim.task_id
        try:
            result = await process_event_task(
                engine,
                claim=claim,
            )
        except TrackerLeaseLostError:
            logger.warning("tracker event task %s lost lease fence; stale worker stops", task_id)
            continue
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "tracker event task %s failed (%s)",
                task_id,
                safe_exception_diagnostic(exc),
            )
            await mark_task_retry(
                engine,
                claim=claim,
                error=safe_exception_diagnostic(exc),
            )
            continue

        if result.received_at:
            latency = max((datetime.now(UTC) - result.received_at).total_seconds(), 0)
            TRACKER_PROCESSING_LATENCY.observe(latency)
        if result.needs_scan_refresh:
            try:
                await enqueue_observer_scan(
                    engine,
                    requested_by="tracker_reconciliation_worker",
                    reason="tracker_event_requires_fresh_meta",
                    idempotency_key=observer_scan_idempotency_key(
                        "tracker-event",
                        str(task_id),
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "tracker event task %s could not enqueue durable observer scan (%s)",
                    task_id,
                    safe_exception_diagnostic(exc),
                )
    await _refresh_queue_metrics(engine)
    return len(claims)


async def _refresh_queue_metrics(engine: AsyncEngine) -> None:
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) FILTER (WHERE q.status IN ('pending', 'retrying', 'running')),
                            (SELECT COUNT(*) FROM adsetpro_postback_events e
                             WHERE e.fb_ad_fk IS NULL AND e.processed_at IS NULL)
                        FROM task_queue q
                        WHERE q.task_type = 'tracker_event_process'
                        """
                    )
                )
            ).one()
        TRACKER_EVENT_BACKLOG.set(int(row[0] or 0))
        TRACKER_UNMATCHED_EVENTS.set(int(row[1] or 0))
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "tracker queue gauges unavailable (%s)",
            safe_exception_diagnostic(exc),
        )


async def main_loop(database_url: str) -> None:
    engine = create_async_engine(database_url, **WORKER_ENGINE_KWARGS)
    stop_event = asyncio.Event()
    wakeup = asyncio.Event()

    def _handle_sigterm() -> None:
        logger.info("tracker worker stop signal received")
        stop_event.set()
        wakeup.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_sigterm)
        except (NotImplementedError, ValueError):
            pass

    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)
    metrics_task = asyncio.create_task(metrics_loop(stop_event))
    listener_task = asyncio.create_task(wakeup_listener(redis_client, stop_event, wakeup))
    next_reconcile = 0.0
    try:
        while not stop_event.is_set():
            try:
                await drain_event_tasks(
                    engine,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "tracker durable queue drain failed (%s)",
                    safe_exception_diagnostic(exc),
                )

            monotonic_now = loop.time()
            if monotonic_now >= next_reconcile:
                try:
                    provider_result = await reconcile_provider_events(engine)
                    logger.info(
                        "tracker provider reconciliation status=%s accepted=%d "
                        "drift_before=%d drift_after=%d",
                        provider_result.status,
                        provider_result.accepted,
                        provider_result.drift_before,
                        provider_result.drift_after,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "tracker reconciliation failed (%s)",
                        safe_exception_diagnostic(exc),
                    )
                next_reconcile = monotonic_now + _RECONCILIATION_INTERVAL_SECONDS

            wakeup.clear()
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=_DB_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        stop_event.set()
        wakeup.set()
        for task in (listener_task, metrics_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            await redis_client.aclose()
        except Exception:  # noqa: BLE001
            pass
        await engine.dispose()
        logger.info("tracker_reconciliation_worker stopped")


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_loop(_get_database_url()))
