# -*- coding: utf-8 -*-
"""Existing tracker worker: live durable-event processing + 5 minute reconciliation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apps.tracker_aggregator_worker.worker import DEFAULT_LOOKBACK, run_once
from core.adset_pro.aggregator import aggregate_affected_event
from core.adset_pro.processing import (
    claim_event_tasks,
    mark_task_retry,
    process_event_task,
    requeue_aggregation_repair,
)
from core.adset_pro.reconciliation import reconcile_provider_events
from core.config import get_settings
from core.db import WORKER_ENGINE_KWARGS
from core.metrics import (
    TRACKER_EVENT_BACKLOG,
    TRACKER_PROCESSING_LATENCY,
    TRACKER_UNMATCHED_EVENTS,
)
from core.pubsub import CHANNEL_TASK_CHANGED, CHANNEL_TRACKER_CHANGED, CHANNEL_TRACKER_WAKEUP

logger = logging.getLogger("tracker_aggregator_worker")

WORKER_NAME = "tracker_aggregator"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60
CHANNEL_OBSERVER_TRIGGER = "fb_agent:observer:trigger"

_INTERVAL_SECONDS = int(os.environ.get("TRACKER_AGGREGATOR_INTERVAL_SECONDS", "300"))
_LOOKBACK_SECONDS = int(
    os.environ.get(
        "TRACKER_AGGREGATOR_LOOKBACK_SECONDS", str(int(DEFAULT_LOOKBACK.total_seconds()))
    )
)
_DB_POLL_SECONDS = float(os.environ.get("TRACKER_EVENT_DB_POLL_SECONDS", "1"))


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


async def heartbeat_loop(redis_client, stop: asyncio.Event) -> None:
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("tracker_aggregator heartbeat: Redis write failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
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
    except Exception:  # noqa: BLE001
        logger.warning("tracker wakeup listener unavailable; using DB polling", exc_info=True)
    finally:
        try:
            await pubsub.unsubscribe(CHANNEL_TRACKER_WAKEUP)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


async def _publish(redis_client, channel: str, payload: dict) -> None:
    try:
        await redis_client.publish(channel, json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.debug("tracker publish failed: %s", channel, exc_info=True)


async def drain_event_tasks(
    engine: AsyncEngine,
    redis_client,
    *,
    limit: int = 100,
    auto_cancel_enabled: bool = False,
) -> int:
    """Drain one claimed batch and publish only after each DB commit."""
    task_ids = await claim_event_tasks(engine, limit=limit)
    for task_id in task_ids:
        try:
            result = await process_event_task(
                engine,
                task_id=task_id,
                auto_cancel_enabled=auto_cancel_enabled,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("tracker event task %s failed", task_id)
            await mark_task_retry(engine, task_id=task_id, error=str(exc))
            continue

        if result.processed and result.fb_ad_id and result.occurred_at:
            try:
                await aggregate_affected_event(
                    engine,
                    occurred_at=result.occurred_at,
                    fb_ad_id=result.fb_ad_id,
                )
            except Exception as exc:  # noqa: BLE001
                # The projection commit precedes aggregation. Requeue the same
                # durable task so an old occurred_at remains repairable even after
                # it falls outside the periodic reconciliation lookback.
                logger.exception("targeted tracker aggregation failed")
                await requeue_aggregation_repair(engine, task_id=task_id, error=str(exc))

        if result.received_at:
            latency = max((datetime.now(UTC) - result.received_at).total_seconds(), 0)
            TRACKER_PROCESSING_LATENCY.observe(latency)
        if result.needs_scan_refresh:
            await _publish(
                redis_client,
                CHANNEL_OBSERVER_TRIGGER,
                {"reason": "tracker_event_requires_fresh_meta", "task_id": task_id},
            )
        for cancelled_id in result.cancelled_task_ids:
            await _publish(
                redis_client,
                CHANNEL_TASK_CHANGED,
                {"task_id": cancelled_id, "task_type": "meta_api_mutation", "status": "cancelled"},
            )
        if result.auto_cancel_shadow_candidate:
            logger.info(
                "tracker shadow auto-cancel candidate task=%s event=%s fb_ad_id=%s",
                task_id,
                result.event_id,
                result.fb_ad_id,
            )
        await _publish(
            redis_client,
            CHANNEL_TRACKER_CHANGED,
            {
                "task_id": task_id,
                "event_id": result.event_id,
                "processed": result.processed,
                "attribution_status": result.attribution_status,
                "fb_ad_id": result.fb_ad_id,
                "cancelled_task_ids": list(result.cancelled_task_ids),
                "auto_cancel_shadow_candidate": result.auto_cancel_shadow_candidate,
            },
        )
    await _refresh_queue_metrics(engine)
    return len(task_ids)


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
    except Exception:  # noqa: BLE001
        logger.debug("tracker queue gauges unavailable", exc_info=True)


async def main_loop(database_url: str) -> None:
    engine = create_async_engine(database_url, **WORKER_ENGINE_KWARGS)
    stop_event = asyncio.Event()
    wakeup = asyncio.Event()
    lookback = timedelta(seconds=_LOOKBACK_SECONDS)
    auto_cancel_enabled = get_settings().tracker_auto_cancel_enabled

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
    heartbeat_task = asyncio.create_task(heartbeat_loop(redis_client, stop_event))
    listener_task = asyncio.create_task(wakeup_listener(redis_client, stop_event, wakeup))
    next_reconcile = 0.0
    try:
        while not stop_event.is_set():
            try:
                await drain_event_tasks(
                    engine,
                    redis_client,
                    auto_cancel_enabled=auto_cancel_enabled,
                )
            except Exception:  # noqa: BLE001
                logger.exception("tracker durable queue drain failed")

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
                    await run_once(engine, lookback=lookback)
                except Exception:  # noqa: BLE001
                    logger.exception("tracker reconciliation failed")
                next_reconcile = monotonic_now + _INTERVAL_SECONDS

            wakeup.clear()
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=_DB_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        stop_event.set()
        wakeup.set()
        for task in (listener_task, heartbeat_task):
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
        logger.info("tracker_aggregator_worker stopped")


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_loop(_get_database_url()))
