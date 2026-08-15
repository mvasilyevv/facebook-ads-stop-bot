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

import time
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Literal, Mapping

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

ADSETPRO_POSTBACK_EVENTS = Counter(
    "fb_agent_adsetpro_postback_events_total",
    "Accepted, duplicate and unsupported AdSet.pro postbacks",
    labelnames=("outcome",),
)

TRACKER_RECONCILIATION_RUNS = Counter(
    "fb_agent_tracker_reconciliation_runs_total",
    "Periodic AdSet.pro provider reconciliation runs",
    labelnames=("outcome",),
)

CLEANUP_RUNS = Counter(
    "fb_agent_cleanup_runs_total",
    "Завершённые cleanup-прогоны по исходу",
    labelnames=("outcome",),
)

# ---------------------------------------------------------------------------
# Gauge'ы
# ---------------------------------------------------------------------------

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

DATABASE_SIZE_BYTES = Gauge(
    "fb_agent_database_size_bytes",
    "Суммарный размер текущей PostgreSQL базы",
)

DATABASE_RELATION_SIZE_BYTES = Gauge(
    "fb_agent_database_relation_size_bytes",
    "Размер крупной таблицы или её партиции с индексами",
    labelnames=("table", "relation", "kind"),
)

DATABASE_OLDEST_PARTITION_AGE = Gauge(
    "fb_agent_database_oldest_partition_age_seconds",
    "Возраст самой старой месячной партиции",
    labelnames=("table",),
)

DATABASE_STORAGE_LAST_REFRESH_TIMESTAMP = Gauge(
    "fb_agent_database_storage_last_refresh_timestamp_seconds",
    "Unix timestamp последнего успешного snapshot размеров PostgreSQL",
)

DATABASE_DISK_FREE_BYTES = Gauge(
    "fb_agent_database_disk_free_bytes",
    "Свободное место на файловой системе Docker/PostgreSQL",
)

DATABASE_DISK_TOTAL_BYTES = Gauge(
    "fb_agent_database_disk_total_bytes",
    "Общий размер файловой системы Docker/PostgreSQL",
)

DATABASE_DISK_CHECK_SUCCESS = Gauge(
    "fb_agent_database_disk_check_success",
    "1, если последняя проверка свободного места успешна, иначе 0",
)

DATABASE_DISK_LAST_CHECK_TIMESTAMP = Gauge(
    "fb_agent_database_disk_last_check_timestamp_seconds",
    "Unix timestamp последней попытки проверить свободное место",
)

CLEANUP_ROWS_DELETED = Gauge(
    "fb_agent_cleanup_rows_deleted",
    "Строки, удалённые за последний cleanup-прогон",
    labelnames=("target",),
)

CLEANUP_PARTITIONS_DROPPED = Gauge(
    "fb_agent_cleanup_partitions_dropped",
    "Партиции, отброшенные за последний cleanup-прогон",
    labelnames=("table",),
)

CLEANUP_LAST_RUN_FINISHED_TIMESTAMP = Gauge(
    "fb_agent_cleanup_last_run_finished_timestamp_seconds",
    "Unix timestamp последнего завершённого cleanup-прогона; 0 означает «не запускался»",
)

CLEANUP_LAST_RUN_SUCCESS = Gauge(
    "fb_agent_cleanup_last_run_success",
    "1, если последний cleanup-прогон завершился без ошибок, иначе 0",
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


def record_database_storage(
    *,
    database_size_bytes: int,
    relation_sizes: Mapping[tuple[str, str, Literal["table", "partition"]], int],
    oldest_partition_ages: Mapping[str, float],
) -> None:
    """Заменяет snapshot размеров, удаляя stale label series."""
    DATABASE_SIZE_BYTES.set(max(0, int(database_size_bytes)))
    DATABASE_RELATION_SIZE_BYTES.clear()
    for (table, relation, kind), size_bytes in relation_sizes.items():
        DATABASE_RELATION_SIZE_BYTES.labels(
            table=table,
            relation=relation,
            kind=kind,
        ).set(max(0, int(size_bytes)))
    DATABASE_OLDEST_PARTITION_AGE.clear()
    for table, age_seconds in oldest_partition_ages.items():
        DATABASE_OLDEST_PARTITION_AGE.labels(table=table).set(max(0.0, age_seconds))
    DATABASE_STORAGE_LAST_REFRESH_TIMESTAMP.set(time.time())


def record_database_disk(*, free_bytes: int, total_bytes: int) -> None:
    """Обновляет место на файловой системе с PostgreSQL volume."""
    DATABASE_DISK_FREE_BYTES.set(max(0, int(free_bytes)))
    DATABASE_DISK_TOTAL_BYTES.set(max(0, int(total_bytes)))
    DATABASE_DISK_CHECK_SUCCESS.set(1)
    DATABASE_DISK_LAST_CHECK_TIMESTAMP.set(time.time())


def record_database_disk_unavailable() -> None:
    """Mark the latest disk measurement attempt as failed without inventing free bytes."""
    DATABASE_DISK_CHECK_SUCCESS.set(0)
    DATABASE_DISK_LAST_CHECK_TIMESTAMP.set(time.time())


def record_cleanup_run(
    *,
    finished_at: datetime,
    success: bool,
    rows_deleted: Mapping[str, int],
    partitions_dropped: Mapping[str, int],
) -> None:
    """Публикует итог одного прогона; zero-label отличается от его отсутствия."""
    if finished_at.tzinfo is None:
        raise ValueError("finished_at must be timezone-aware")
    CLEANUP_ROWS_DELETED.clear()
    for target, count in rows_deleted.items():
        CLEANUP_ROWS_DELETED.labels(target=target).set(max(0, int(count)))
    CLEANUP_PARTITIONS_DROPPED.clear()
    for table, count in partitions_dropped.items():
        CLEANUP_PARTITIONS_DROPPED.labels(table=table).set(max(0, int(count)))
    CLEANUP_LAST_RUN_FINISHED_TIMESTAMP.set(finished_at.timestamp())
    CLEANUP_LAST_RUN_SUCCESS.set(1 if success else 0)
    CLEANUP_RUNS.labels(outcome="success" if success else "failed").inc()
