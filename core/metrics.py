# -*- coding: utf-8 -*-
"""Prometheus-метрики FB Agent.

Все метрики регистрируются в дефолтном REGISTRY при импорте модуля.
Использование:

    from core.metrics import scan_timer, record_vision_failure, record_alert_sent

    async def scan():
        with scan_timer():
            ...

    record_vision_failure()
    record_alert_sent(elapsed_ms=340.5)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Гистограммы
# ---------------------------------------------------------------------------

SCAN_DURATION = Histogram(
    "fb_agent_scan_duration_seconds",
    "Длительность observer scan-цикла",
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300),
)

ALERT_SEND_LATENCY = Histogram(
    "fb_agent_alert_send_latency_ms",
    "Время от создания алёрта до отправки в Telegram (мс)",
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000),
)

TRACKER_PROCESSING_LATENCY = Histogram(
    "fb_agent_tracker_processing_latency_seconds",
    "Postback receive-to-projection latency",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)

# ---------------------------------------------------------------------------
# Счётчики
# ---------------------------------------------------------------------------

VISION_FAILURES = Counter(
    "fb_agent_vision_failures_total",
    "Счётчик фейлов Vision API",
)

OBSERVER_CYCLES = Counter(
    "fb_agent_observer_cycles_total",
    "Счётчик завершённых scan-циклов",
    labelnames=("outcome",),
)

TRACKER_POSTBACK_EVENTS = Counter(
    "fb_agent_tracker_postback_events_total",
    "Accepted, duplicate and unsupported AdSet.pro postbacks",
    labelnames=("outcome",),
)

TRACKER_RECONCILIATION_RUNS = Counter(
    "fb_agent_tracker_reconciliation_runs_total",
    "Periodic AdSet.pro provider reconciliation runs",
    labelnames=("outcome",),
)

# ---------------------------------------------------------------------------
# Gauge'ы
# ---------------------------------------------------------------------------

WORKER_HEARTBEAT_AGE = Gauge(
    "fb_agent_worker_heartbeat_age_seconds",
    "Возраст последнего heartbeat воркера (секунды)",
    labelnames=("worker",),
)

DISABLE_TASKS_PENDING = Gauge(
    "fb_agent_disable_tasks_pending",
    "Количество DisableTask в статусе pending/retrying",
)

ENABLE_TASKS_PENDING = Gauge(
    "fb_agent_enable_tasks_pending",
    "Количество EnableTask в статусе pending/retrying",
)

TRACKER_EVENT_BACKLOG = Gauge(
    "fb_agent_tracker_event_backlog",
    "Runnable or retrying tracker_event_process tasks",
)

TRACKER_UNMATCHED_EVENTS = Gauge(
    "fb_agent_tracker_unmatched_events",
    "Accepted tracker events that still have no ad attribution",
)

TRACKER_PROVIDER_RECONCILIATION_DRIFT = Gauge(
    "fb_agent_tracker_provider_reconciliation_drift",
    "Canonical provider facts missing from or extra in the local inbox after reconciliation",
)


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------


@contextmanager
def scan_timer() -> Generator[None, None, None]:
    """Context manager: измеряет длительность scan-цикла через SCAN_DURATION.

    Пример использования::

        with scan_timer():
            await do_scan()
    """
    with SCAN_DURATION.time():
        yield


def record_vision_failure() -> None:
    """Инкрементит счётчик фейлов Vision API."""
    VISION_FAILURES.inc()


def record_alert_sent(elapsed_ms: float) -> None:
    """Фиксирует latency доставки алёрта в Telegram.

    Args:
        elapsed_ms: Время от создания алёрта до успешной отправки (миллисекунды).
    """
    ALERT_SEND_LATENCY.observe(elapsed_ms)
