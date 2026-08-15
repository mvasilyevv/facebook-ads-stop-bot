# -*- coding: utf-8 -*-
"""Storage snapshot, Prometheus projection and durable cleanup incidents."""

from __future__ import annotations

import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.cleanup_worker.retention import cutoff_datetime, is_special
from core.metrics import (
    record_database_disk,
    record_database_disk_unavailable,
    record_database_storage,
)
from core.telegram.worker_notify import notify_recurring_incident, resolve_recurring_incident
from core.wording import counted_ru, days_ru, errors_ru, hours_ru, human_bytes_ru

RETENTION_LAG_INCIDENT_KEY = "storage:retention-lag"
DISK_SPACE_INCIDENT_KEY = "storage:disk-space-low"
CLEANUP_RUN_INCIDENT_KEY = "storage:cleanup-run"
CLEANUP_STALE_INCIDENT_KEY = "storage:cleanup-stale"

DEFAULT_DISK_PATH = "/"
DEFAULT_MIN_DISK_FREE_BYTES = 10 * 1024**3
DEFAULT_MIN_DISK_FREE_RATIO = 0.10
DEFAULT_CLEANUP_MAX_AGE = timedelta(days=1)

# Durable, potentially growing relations that cleanup already owns.  The list
# is deliberately finite so Prometheus label cardinality stays bounded.
MONITORED_TABLES: tuple[str, ...] = (
    "ad_metrics",
    "alert_events",
    "scan_runs",
    "meta_api_audit_log",
    "adsetpro_postback_events",
    "task_queue",
    "adset_duplicate_previews",
    "browser_operation_leases",
    "browser_operation_capability_uses",
    "telegram_invites",
    "operator_revision_events",
    "incidents",
    "notification_events",
    "notification_deliveries",
    "telegram_action_tokens",
    "telegram_navigation_tokens",
    "telegram_updates_inbox",
    "telegram_command_replies",
)

_MONTH_PARTITION_RE = re.compile(r"^(?P<table>[a-z0-9_]+)_(?P<year>\d{4})_(?P<month>\d{2})$")


@dataclass(frozen=True)
class RelationSize:
    table_name: str
    relation_name: str
    kind: Literal["table", "partition"]
    size_bytes: int
    partition_started_at: datetime | None = None
    partition_ends_at: datetime | None = None


@dataclass(frozen=True)
class DatabaseStorageSnapshot:
    database_size_bytes: int
    relations: tuple[RelationSize, ...]


@dataclass(frozen=True)
class DiskSpace:
    path: str
    total_bytes: int
    free_bytes: int


@dataclass(frozen=True)
class CleanupRunAudit:
    finished_at: datetime
    outcome: Literal["success", "failed"]
    error_count: int


def _partition_bounds(table_name: str, relation_name: str) -> tuple[datetime, datetime] | None:
    match = _MONTH_PARTITION_RE.fullmatch(relation_name)
    if match is None or match.group("table") != table_name:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    if not 1 <= month <= 12:
        return None
    started_at = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        ends_at = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        ends_at = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return started_at, ends_at


async def collect_database_storage(engine: AsyncEngine) -> DatabaseStorageSnapshot:
    """Read total DB and per-relation sizes in one catalog pass."""
    async with engine.connect() as conn:
        database_size_bytes = int(
            (await conn.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()
        )
        result = await conn.execute(
            text(
                """
                WITH wanted(table_name) AS (
                    SELECT unnest(CAST(:tables AS text[]))
                ), parents AS (
                    SELECT wanted.table_name, relation.oid AS relation_oid
                    FROM wanted
                    JOIN pg_class relation ON relation.relname = wanted.table_name
                    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND relation.relkind IN ('p', 'r')
                ), children AS (
                    SELECT parents.table_name,
                           child.oid AS relation_oid,
                           child.relname AS relation_name
                    FROM parents
                    JOIN pg_inherits inherits ON inherits.inhparent = parents.relation_oid
                    JOIN pg_class child ON child.oid = inherits.inhrelid
                ), parent_sizes AS (
                    SELECT parents.table_name,
                           pg_total_relation_size(parents.relation_oid)
                               + COALESCE(SUM(pg_total_relation_size(children.relation_oid)), 0)
                                   AS size_bytes
                    FROM parents
                    LEFT JOIN children ON children.table_name = parents.table_name
                    GROUP BY parents.table_name, parents.relation_oid
                )
                SELECT table_name, table_name AS relation_name, 'table' AS kind, size_bytes
                FROM parent_sizes
                UNION ALL
                SELECT table_name, relation_name, 'partition' AS kind,
                       pg_total_relation_size(relation_oid) AS size_bytes
                FROM children
                ORDER BY table_name, kind, relation_name
                """
            ),
            {"tables": list(MONITORED_TABLES)},
        )
        rows = result.mappings().all()

    relations: list[RelationSize] = []
    for row in rows:
        table_name = str(row["table_name"])
        relation_name = str(row["relation_name"])
        kind = str(row["kind"])
        if kind == "partition":
            bounds = _partition_bounds(table_name, relation_name)
        else:
            bounds = None
        relations.append(
            RelationSize(
                table_name=table_name,
                relation_name=relation_name,
                kind="partition" if kind == "partition" else "table",
                size_bytes=int(row["size_bytes"] or 0),
                partition_started_at=bounds[0] if bounds else None,
                partition_ends_at=bounds[1] if bounds else None,
            )
        )
    return DatabaseStorageSnapshot(
        database_size_bytes=database_size_bytes,
        relations=tuple(relations),
    )


def collect_disk_space(path: str | None = None) -> DiskSpace:
    """Measure the filesystem backing the cleanup container and Docker volumes."""
    measured_path = path or os.environ.get("CLEANUP_DISK_PATH", DEFAULT_DISK_PATH)
    usage = shutil.disk_usage(measured_path)
    return DiskSpace(
        path=measured_path,
        total_bytes=int(usage.total),
        free_bytes=int(usage.free),
    )


def disk_thresholds_from_env() -> tuple[int, float]:
    min_free_bytes = int(
        os.environ.get("CLEANUP_MIN_DISK_FREE_BYTES", str(DEFAULT_MIN_DISK_FREE_BYTES))
    )
    min_free_percent = float(
        os.environ.get(
            "CLEANUP_MIN_DISK_FREE_PERCENT",
            str(DEFAULT_MIN_DISK_FREE_RATIO * 100),
        )
    )
    if min_free_bytes < 0:
        raise ValueError("CLEANUP_MIN_DISK_FREE_BYTES must be non-negative")
    if not 0 <= min_free_percent <= 100:
        raise ValueError("CLEANUP_MIN_DISK_FREE_PERCENT must be within 0..100")
    return min_free_bytes, min_free_percent / 100


def _disk_threshold_bytes(
    disk: DiskSpace,
    *,
    min_free_bytes: int,
    min_free_ratio: float,
) -> int:
    if min_free_bytes < 0 or not 0 <= min_free_ratio <= 1:
        raise ValueError("disk thresholds must be non-negative and ratio within 0..1")
    return max(int(min_free_bytes), math.ceil(disk.total_bytes * min_free_ratio))


def overdue_partitions(
    snapshot: DatabaseStorageSnapshot,
    policy: dict[str, str],
    *,
    now: datetime,
) -> tuple[RelationSize, ...]:
    """Return monthly partitions whose complete range is past retention."""
    overdue: list[RelationSize] = []
    for relation in snapshot.relations:
        if relation.kind != "partition" or relation.partition_ends_at is None:
            continue
        retention = policy.get(relation.table_name)
        if not retention or is_special(retention):
            continue
        if relation.partition_ends_at <= cutoff_datetime(retention, now=now):
            overdue.append(relation)
    return tuple(overdue)


def publish_database_metrics(
    snapshot: DatabaseStorageSnapshot,
    *,
    now: datetime,
) -> None:
    relation_sizes = {
        (relation.table_name, relation.relation_name, relation.kind): relation.size_bytes
        for relation in snapshot.relations
    }
    oldest_started_at: dict[str, datetime] = {}
    for relation in snapshot.relations:
        if relation.partition_started_at is None:
            continue
        current = oldest_started_at.get(relation.table_name)
        if current is None or relation.partition_started_at < current:
            oldest_started_at[relation.table_name] = relation.partition_started_at
    record_database_storage(
        database_size_bytes=snapshot.database_size_bytes,
        relation_sizes=relation_sizes,
        oldest_partition_ages={
            table: max(0.0, (now - started_at).total_seconds())
            for table, started_at in oldest_started_at.items()
        },
    )


async def publish_retention_health(
    engine: AsyncEngine,
    *,
    snapshot: DatabaseStorageSnapshot,
    policy: dict[str, str],
    now: datetime,
) -> bool:
    expired = overdue_partitions(snapshot, policy, now=now)
    if not expired:
        return await resolve_recurring_incident(
            engine,
            incident_key=RETENTION_LAG_INCIDENT_KEY,
            audience="owners",
            summary="Старые данные снова удаляются по расписанию.",
        )

    lag_days = 1
    for relation in expired:
        retention = policy[relation.table_name]
        assert relation.partition_ends_at is not None
        lag = cutoff_datetime(retention, now=now) - relation.partition_ends_at
        lag_days = max(lag_days, math.ceil(max(0.0, lag.total_seconds()) / 86400))
    return await notify_recurring_incident(
        engine,
        incident_key=RETENTION_LAG_INCIDENT_KEY,
        audience="owners",
        event_type="cleanup_retention_lag",
        severity="warning",
        title="Уборка старых данных отстаёт",
        summary=(
            "После cleanup осталось "
            + counted_ru(
                len(expired),
                "просроченная партиция",
                "просроченные партиции",
                "просроченных партиций",
            )
            + "."
        ),
        lines=(
            f"Отставание: минимум {days_ru(lag_days)}.",
            "Что делать: проверить cleanup worker и ошибки PostgreSQL.",
        ),
        risk="Диск продолжит заполняться; скан и авто-стоп могут остановиться.",
        resource_type="storage",
        resource_id="retention",
    )


async def publish_disk_health(
    engine: AsyncEngine,
    *,
    disk: DiskSpace,
    min_free_bytes: int,
    min_free_ratio: float,
) -> bool:
    record_database_disk(free_bytes=disk.free_bytes, total_bytes=disk.total_bytes)
    threshold = _disk_threshold_bytes(
        disk,
        min_free_bytes=min_free_bytes,
        min_free_ratio=min_free_ratio,
    )
    if disk.free_bytes >= threshold:
        return await resolve_recurring_incident(
            engine,
            incident_key=DISK_SPACE_INCIDENT_KEY,
            audience="owners",
            summary="Свободного места снова достаточно.",
        )

    percent = (disk.free_bytes / disk.total_bytes * 100) if disk.total_bytes else 0.0
    percent_text = f"{percent:.1f}".replace(".", ",")
    return await notify_recurring_incident(
        engine,
        incident_key=DISK_SPACE_INCIDENT_KEY,
        audience="owners",
        event_type="database_disk_space_low",
        severity="critical",
        title="На диске заканчивается место",
        summary=(
            f"Свободно {human_bytes_ru(disk.free_bytes)} из "
            f"{human_bytes_ru(disk.total_bytes)} ({percent_text} %)."
        ),
        lines=(
            f"Критический порог: {human_bytes_ru(threshold)}.",
            "Что делать: освободить место и проверить рост PostgreSQL и docker-логов.",
        ),
        risk="При заполнении диска PostgreSQL остановится вместе со сканом и авто-стопом.",
        resource_type="storage",
        resource_id="database-disk",
    )


async def publish_disk_check_unavailable(engine: AsyncEngine) -> bool:
    """Fail closed when free space cannot be measured at all."""
    record_database_disk_unavailable()
    return await notify_recurring_incident(
        engine,
        incident_key=DISK_SPACE_INCIDENT_KEY,
        audience="owners",
        event_type="database_disk_space_unknown",
        severity="critical",
        title="Не удалось проверить свободное место",
        summary="Свободное место под PostgreSQL сейчас неизвестно.",
        lines=("Что делать: проверить cleanup worker, путь диска и node-exporter.",),
        risk="Без контроля места PostgreSQL может остановиться без предупреждения.",
        resource_type="storage",
        resource_id="database-disk",
    )


def _count_errors(counts: dict[str, object]) -> int:
    return sum(1 for key in counts if key.endswith("_error"))


async def load_last_cleanup_run(engine: AsyncEngine) -> CleanupRunAudit | None:
    async with engine.connect() as conn:
        value = (
            await conn.execute(
                text("SELECT value FROM system_config WHERE key = :key"),
                {"key": "cleanup_runs"},
            )
        ).scalar_one_or_none()
    if not isinstance(value, dict):
        return None
    raw_finished_at = value.get("last_run_finished_at")
    if not isinstance(raw_finished_at, str):
        return None
    try:
        finished_at = datetime.fromisoformat(raw_finished_at)
    except ValueError:
        return None
    if finished_at.tzinfo is None:
        return None
    counts = value.get("counts")
    error_count = _count_errors(counts) if isinstance(counts, dict) else 0
    raw_outcome = value.get("outcome")
    if raw_outcome not in {None, "success", "failed"}:
        return None
    outcome: Literal["success", "failed"] = (
        "failed" if raw_outcome == "failed" or error_count else "success"
    )
    return CleanupRunAudit(
        finished_at=finished_at.astimezone(timezone.utc),
        outcome=outcome,
        error_count=error_count,
    )


async def publish_cleanup_run_health(
    engine: AsyncEngine,
    *,
    success: bool,
    error_count: int,
) -> bool:
    if success:
        return await resolve_recurring_incident(
            engine,
            incident_key=CLEANUP_RUN_INCIDENT_KEY,
            audience="owners",
            summary="Ежедневная уборка снова завершается без ошибок.",
        )
    return await notify_recurring_incident(
        engine,
        incident_key=CLEANUP_RUN_INCIDENT_KEY,
        audience="owners",
        event_type="cleanup_run_failed",
        severity="warning",
        title="Ежедневная уборка данных не завершилась",
        summary=f"В последнем прогоне возникло {errors_ru(max(1, error_count))}.",
        lines=("Что делать: проверить логи cleanup worker и доступность PostgreSQL.",),
        risk="Старые данные продолжат занимать диск.",
        resource_type="worker",
        resource_id="cleanup",
    )


async def publish_cleanup_freshness(
    engine: AsyncEngine,
    *,
    now: datetime,
    max_age: timedelta = DEFAULT_CLEANUP_MAX_AGE,
) -> bool:
    audit = await load_last_cleanup_run(engine)
    if audit is not None and audit.finished_at > now:
        audit = None
    if audit is not None and audit.outcome == "failed":
        return await publish_cleanup_run_health(
            engine,
            success=False,
            error_count=audit.error_count,
        )
    if audit is not None and now - audit.finished_at <= max_age:
        return await resolve_recurring_incident(
            engine,
            incident_key=CLEANUP_STALE_INCIDENT_KEY,
            audience="owners",
            summary="Ежедневная уборка снова выполняется по расписанию.",
        )

    if audit is None:
        summary = "Нет подтверждённого завершённого прогона cleanup."
        lines = ("Что делать: проверить, запущен ли cleanup worker, и открыть его логи.",)
        title = "Ежедневная уборка данных не запускалась"
    else:
        age_hours = max(1, math.floor((now - audit.finished_at).total_seconds() / 3600))
        summary = f"Последний успешный прогон был {hours_ru(age_hours)} назад."
        lines = ("Что делать: проверить расписание и логи cleanup worker.",)
        title = "Ежедневная уборка данных опоздала"
    return await notify_recurring_incident(
        engine,
        incident_key=CLEANUP_STALE_INCIDENT_KEY,
        audience="owners",
        event_type="cleanup_run_stale",
        severity="warning",
        title=title,
        summary=summary,
        lines=lines,
        risk="Без уборки база и диск продолжают расти.",
        resource_type="worker",
        resource_id="cleanup",
    )


__all__ = [
    "CLEANUP_RUN_INCIDENT_KEY",
    "CLEANUP_STALE_INCIDENT_KEY",
    "DEFAULT_CLEANUP_MAX_AGE",
    "DEFAULT_MIN_DISK_FREE_BYTES",
    "DEFAULT_MIN_DISK_FREE_RATIO",
    "DISK_SPACE_INCIDENT_KEY",
    "DatabaseStorageSnapshot",
    "DiskSpace",
    "RelationSize",
    "RETENTION_LAG_INCIDENT_KEY",
    "collect_database_storage",
    "collect_disk_space",
    "disk_thresholds_from_env",
    "load_last_cleanup_run",
    "overdue_partitions",
    "publish_cleanup_freshness",
    "publish_cleanup_run_health",
    "publish_database_metrics",
    "publish_disk_check_unavailable",
    "publish_disk_health",
    "publish_retention_health",
]
