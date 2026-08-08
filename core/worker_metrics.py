# -*- coding: utf-8 -*-
"""Low-cardinality Prometheus metrics shared by long-running workers."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Literal

from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)

WORKER_HEARTBEAT = Gauge(
    "fb_agent_worker_heartbeat_timestamp_seconds",
    "Unix timestamp of the latest in-process worker heartbeat",
    ("worker",),
)
TASK_CLAIM_LATENCY = Histogram(
    "fb_agent_task_claim_latency_seconds",
    "PostgreSQL durable task claim latency",
    ("lane", "task_type"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
TASK_LEASE_STEALS = Counter(
    "fb_agent_task_lease_steals_total",
    "Expired task leases reclaimed by a worker",
    ("lane",),
)
TASK_QUEUE_DEPTH = Gauge(
    "fb_agent_task_queue_depth",
    "Durable task rows by scheduler lane and state",
    ("lane", "status"),
)
TASK_OLDEST_PENDING_AGE = Gauge(
    "fb_agent_task_oldest_pending_age_seconds",
    "Age of the oldest runnable task in a lane",
    ("lane",),
)
SNAPSHOT_AGE = Gauge(
    "fb_agent_snapshot_age_seconds",
    "Age of the latest canonical source snapshot",
    ("source",),
)
NOTIFICATION_OLDEST_PENDING_AGE = Gauge(
    "fb_agent_notification_oldest_pending_age_seconds",
    "Age from event commit to the oldest due notification delivery",
    ("severity",),
)
NOTIFICATION_DELIVERIES = Counter(
    "fb_agent_notification_deliveries",
    "Fenced notification delivery state transitions",
    ("state", "severity"),
)
NOTIFICATION_DELIVERY_LATENCY = Histogram(
    "fb_agent_notification_delivery_latency_seconds",
    "Notification event commit to terminal delivery latency",
    ("state", "severity"),
    buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30, 60, 120, 300, 900),
)
NOTIFICATION_TERMINAL_ROWS = Gauge(
    "fb_agent_notification_delivery_terminal_rows",
    "Durable terminal notification delivery rows by state in the rolling 7d window",
    ("state",),
)
NOTIFICATION_TERMINAL_RECENT = Gauge(
    "fb_agent_notification_delivery_terminal_events_5m",
    "Durable notification deliveries terminally updated during the last five minutes",
    ("state",),
)
NOTIFICATION_LATENCY_QUANTILE = Gauge(
    "fb_agent_notification_delivery_latency_quantile_seconds",
    "Durable event-commit to terminal-delivery latency quantiles over rolling 7d",
    ("state", "quantile"),
)
NOTIFICATION_METRICS_LAST_REFRESH = Gauge(
    "fb_agent_notification_metrics_last_refresh_timestamp_seconds",
    "Unix timestamp of the last successful PostgreSQL notification metric refresh",
)
IRREVERSIBLE_TASK_OUTCOMES = Counter(
    "fb_agent_irreversible_task_outcomes_total",
    "Terminal irreversible task outcomes after a fenced finalization",
    ("worker", "task_type", "outcome"),
)
IRREVERSIBLE_TASK_DURATION = Histogram(
    "fb_agent_irreversible_task_duration_seconds",
    "End-to-end processing latency for terminal irreversible tasks",
    ("worker", "task_type", "outcome"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 900, 1800),
)
IRREVERSIBLE_TASK_SAFETY_EVENTS = Counter(
    "fb_agent_irreversible_task_safety_events_total",
    "Low-cardinality safety boundary and fencing events",
    ("worker", "task_type", "event"),
)

IrreversibleOutcome = Literal["CONFIRMED", "REJECTED", "UNKNOWN"]
IrreversibleSafetyEvent = Literal[
    "external_boundary",
    "pre_boundary_stop",
    "stale_fence",
    "ambiguous_no_retry",
]
NotificationTerminalState = Literal["sent", "dead", "unknown", "superseded"]

_START_LOCK = Lock()
_IS_STARTED = False


def start_worker_metrics_server(worker_name: str, *, port: int | None = None) -> None:
    """Start the process-local metrics endpoint once.

    Ports remain inside the Compose network.  Tests can disable the listener
    with ``WORKER_METRICS_ENABLED=0`` without changing metric registration.
    """
    global _IS_STARTED
    if os.environ.get("WORKER_METRICS_ENABLED", "1") != "1":
        return
    with _START_LOCK:
        if _IS_STARTED:
            return
        listen_port = port or int(os.environ.get("WORKER_METRICS_PORT", "9464"))
        start_http_server(listen_port, addr="0.0.0.0")
        _IS_STARTED = True
        logger.info("worker metrics listening on :%d (%s)", listen_port, worker_name)
    mark_worker_heartbeat(worker_name)


def mark_worker_heartbeat(worker_name: str) -> None:
    WORKER_HEARTBEAT.labels(worker=worker_name).set(time.time())


def record_irreversible_task_outcome(
    worker_name: str,
    task_type: str,
    outcome: IrreversibleOutcome,
    *,
    duration_seconds: float | None = None,
) -> None:
    """Record a terminal result only after its fenced database write succeeds."""
    IRREVERSIBLE_TASK_OUTCOMES.labels(
        worker=worker_name,
        task_type=task_type,
        outcome=outcome,
    ).inc()
    if duration_seconds is not None:
        IRREVERSIBLE_TASK_DURATION.labels(
            worker=worker_name,
            task_type=task_type,
            outcome=outcome,
        ).observe(max(0.0, duration_seconds))


def record_irreversible_safety_event(
    worker_name: str,
    task_type: str,
    event: IrreversibleSafetyEvent,
) -> None:
    IRREVERSIBLE_TASK_SAFETY_EVENTS.labels(
        worker=worker_name,
        task_type=task_type,
        event=event,
    ).inc()


def record_notification_delivery_transition(
    state: str,
    severity: str,
    *,
    event_created_at: datetime | None = None,
    count: int = 1,
) -> None:
    """Record only after a lease-fenced delivery transition commits."""

    if count <= 0:
        return
    NOTIFICATION_DELIVERIES.labels(state=state, severity=severity).inc(count)
    if event_created_at is None or state not in {
        "sent",
        "dead",
        "unknown",
        "superseded",
    }:
        return
    if event_created_at.tzinfo is None:
        raise ValueError("event_created_at must be timezone-aware")
    duration = (datetime.now(timezone.utc) - event_created_at).total_seconds()
    NOTIFICATION_DELIVERY_LATENCY.labels(state=state, severity=severity).observe(max(0.0, duration))


__all__ = [
    "SNAPSHOT_AGE",
    "IRREVERSIBLE_TASK_DURATION",
    "IRREVERSIBLE_TASK_OUTCOMES",
    "IRREVERSIBLE_TASK_SAFETY_EVENTS",
    "NOTIFICATION_DELIVERIES",
    "NOTIFICATION_DELIVERY_LATENCY",
    "NOTIFICATION_LATENCY_QUANTILE",
    "NOTIFICATION_METRICS_LAST_REFRESH",
    "NOTIFICATION_OLDEST_PENDING_AGE",
    "NOTIFICATION_TERMINAL_RECENT",
    "NOTIFICATION_TERMINAL_ROWS",
    "TASK_CLAIM_LATENCY",
    "TASK_LEASE_STEALS",
    "TASK_OLDEST_PENDING_AGE",
    "TASK_QUEUE_DEPTH",
    "WORKER_HEARTBEAT",
    "mark_worker_heartbeat",
    "record_irreversible_safety_event",
    "record_irreversible_task_outcome",
    "record_notification_delivery_transition",
    "start_worker_metrics_server",
]
