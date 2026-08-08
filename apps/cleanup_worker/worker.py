# -*- coding: utf-8 -*-
"""Логика одного прогона cleanup-воркера.

Один прогон = одна транзакция на каждую операцию (независимость операций друг от друга).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.cleanup_worker.retention import (
    cutoff_datetime,
    get_default_policy,
    is_special,
)
from core.auth.panel_access import cleanup_expired_panel_auth_records

logger = logging.getLogger(__name__)

# 5 партиционированных таблиц + столбец-партиция + ключ в retention_policy
_PARTITIONED: list[tuple[str, str, str]] = [
    ("ad_metrics", "cycle_ts", "ad_metrics"),
    ("alert_events", "created_at", "alert_events"),
    ("scan_runs", "started_at", "scan_runs"),
    ("meta_api_audit_log", "created_at", "meta_api_audit_log"),
    ("adsetpro_postback_events", "received_at", "adsetpro_postback_events"),
]


async def load_policy(engine: AsyncEngine) -> dict[str, str]:
    """Читает retention_policy из system_config, иначе дефолт."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT value FROM system_config WHERE key = :k"),
            {"k": "retention_policy"},
        )
        row = result.first()
        if row and row[0]:
            return dict(row[0])
    logger.warning("system_config.retention_policy не найден — использую default")
    return get_default_policy()


async def drop_old_partitions(
    engine: AsyncEngine, policy: dict[str, str], *, now: datetime | None = None
) -> dict[str, int]:
    """Для каждой партиционированной таблицы — DROP партиций старше retention.

    Партиции называются вида <table>_YYYY_MM.
    Возвращает {table: dropped_count}.
    """
    now = now or datetime.now(timezone.utc)
    dropped: dict[str, int] = {}

    for table, _col, policy_key in _PARTITIONED:
        retention = policy.get(policy_key)
        if not retention or is_special(retention):
            continue
        cutoff = cutoff_datetime(retention, now=now)
        async with engine.begin() as conn:
            # Поиск партиций
            result = await conn.execute(
                text(
                    """
                    SELECT child.relname AS partition_name
                    FROM pg_inherits
                    JOIN pg_class parent ON parent.oid = pg_inherits.inhparent
                    JOIN pg_class child ON child.oid = pg_inherits.inhrelid
                    WHERE parent.relname = :parent
                    """
                ),
                {"parent": table},
            )
            partitions = [r[0] for r in result]
            count_dropped = 0
            for part in partitions:
                # Имя вида ad_metrics_2026_05 → парсим YYYY_MM
                parts = part.rsplit("_", 2)
                if len(parts) < 3:
                    continue
                try:
                    year = int(parts[-2])
                    month = int(parts[-1])
                except ValueError:
                    continue
                # Дата начала следующего месяца — если она <= cutoff, партиция целиком устарела
                if month == 12:
                    next_month_start = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
                else:
                    next_month_start = datetime(year, month + 1, 1, tzinfo=timezone.utc)
                if next_month_start <= cutoff:
                    logger.info("DROP PARTITION %s (older than %s)", part, retention)
                    await conn.execute(text(f"DROP TABLE IF EXISTS {part}"))
                    count_dropped += 1
            dropped[table] = count_dropped

    return dropped


async def _partition_exists(conn, part_name: str) -> bool:
    """True если таблица-партиция уже существует (to_regclass не NULL)."""
    return (
        await conn.execute(text("SELECT to_regclass(:n)"), {"n": part_name})
    ).scalar() is not None


async def _create_month_partition(
    engine: AsyncEngine, table: str, col: str, fr: str, to: str
) -> bool:
    """Создаёт месячную партицию table для [fr, to). Возвращает True если реально создана.

    M-5 (аудит 2026-07-12): если строки уже попали в <table>_default (пропуск партиции
    при даунтайме на стыке месяца), обычный CREATE ... PARTITION OF падает
    constraint-violation'ом (Postgres валидирует, что default не содержит строк нового
    диапазона), и это раньше обрывало создание партиций для ЭТОЙ и всех следующих
    таблиц. Восстановление: DETACH default → CREATE партиции → перелив пересекающихся
    строк из default в родителя (роутятся в новую партицию) → ATTACH default.
    """
    part_name = f"{table}_{fr[:4]}_{fr[5:7]}"
    default_name = f"{table}_default"
    async with engine.begin() as conn:
        if await _partition_exists(conn, part_name):
            return False
        try:
            await conn.execute(
                text(
                    f"CREATE TABLE {part_name} PARTITION OF {table} "
                    f"FOR VALUES FROM ('{fr}') TO ('{to}')"
                )
            )
            return True
        except (IntegrityError, ProgrammingError, InternalError) as exc:
            # Скорее всего конфликт со строками в default — идём по detach-пути.
            logger.warning(
                "create partition %s: прямой CREATE не прошёл (%s) — пробуем через detach default",
                part_name,
                exc.__class__.__name__,
            )

    # Detach-recovery в отдельной транзакции (default мог не существовать → тогда
    # исходная ошибка была реальной, пробрасываем её).
    async with engine.begin() as conn:
        if not await _partition_exists(conn, default_name):
            # default нет — конфликт был не из-за него, создаём как есть (пусть упадёт явно).
            await conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {part_name} PARTITION OF {table} "
                    f"FOR VALUES FROM ('{fr}') TO ('{to}')"
                )
            )
            return True
        await conn.execute(text(f"ALTER TABLE {table} DETACH PARTITION {default_name}"))
        await conn.execute(
            text(
                f"CREATE TABLE {part_name} PARTITION OF {table} "
                f"FOR VALUES FROM ('{fr}') TO ('{to}')"
            )
        )
        moved = await conn.execute(
            text(
                f"WITH moved AS ("
                f"  DELETE FROM {default_name} WHERE {col} >= '{fr}' AND {col} < '{to}' RETURNING *"
                f") INSERT INTO {table} SELECT * FROM moved"
            )
        )
        await conn.execute(text(f"ALTER TABLE {table} ATTACH PARTITION {default_name} DEFAULT"))
        logger.warning(
            "create partition %s: восстановлено через detach default, перелито %d строк",
            part_name,
            moved.rowcount or 0,
        )
    return True


async def create_next_partition_if_missing(
    engine: AsyncEngine,
    *,
    now: datetime | None = None,
    fail_on_error: bool = False,
) -> dict[str, int]:
    """Создаёт партиции на текущий + следующий месяц для всех партиционированных таблиц.

    Каждая таблица обрабатывается изолированно: ошибка на одной (напр. неожиданный
    constraint) логируется и НЕ обрывает создание партиций для остальных (M-5).
    """
    now = now or datetime.now(timezone.utc)
    created: dict[str, int] = {}
    failures: list[str] = []

    months_to_ensure: list[tuple[int, int]] = [(now.year, now.month)]
    if now.month == 12:
        months_to_ensure.append((now.year + 1, 1))
    else:
        months_to_ensure.append((now.year, now.month + 1))

    for table, col, _key in _PARTITIONED:
        count_created = 0
        for year, month in months_to_ensure:
            if month == 12:
                next_year, next_month = year + 1, 1
            else:
                next_year, next_month = year, month + 1
            fr = f"{year:04d}-{month:02d}-01"
            to = f"{next_year:04d}-{next_month:02d}-01"
            try:
                if await _create_month_partition(engine, table, col, fr, to):
                    count_created += 1
            except Exception as exc:  # noqa: BLE001 — одна таблица не должна валить остальные
                logger.exception(
                    "create partition для %s [%s..%s) не удалось: %s", table, fr, to, exc
                )
                failures.append(f"{table}[{fr},{to}): {exc}")
        created[table] = count_created
    if failures and fail_on_error:
        raise RuntimeError("startup partition preparation failed: " + "; ".join(failures))
    return created


async def delete_task_queue_completed(
    engine: AsyncEngine, policy: dict[str, str], *, now: datetime | None = None
) -> int:
    """DELETE из task_queue по retention."""
    succeeded_retention = policy.get("task_queue_completed", "30 days")
    failed_retention = policy.get("task_queue_failed", "90 days")
    deleted_total = 0

    if not is_special(succeeded_retention):
        cutoff = cutoff_datetime(succeeded_retention, now=now)
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "DELETE FROM task_queue WHERE status = 'succeeded' AND completed_at < :cutoff"
                ),
                {"cutoff": cutoff},
            )
            deleted_total += result.rowcount or 0

    if not is_special(failed_retention):
        cutoff = cutoff_datetime(failed_retention, now=now)
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "DELETE FROM task_queue "
                    "WHERE status IN ('failed', 'cancelled') AND completed_at < :cutoff"
                ),
                {"cutoff": cutoff},
            )
            deleted_total += result.rowcount or 0

    return deleted_total


async def delete_expired_browser_operation_leases(engine: AsyncEngine) -> int:
    """Delete direct-operation rows only after their durable lease has expired."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                DELETE FROM browser_operation_leases
                WHERE lease_expires_at <= clock_timestamp()
                """
            )
        )
        return int(result.rowcount or 0)


async def delete_expired_adset_duplicate_previews(engine: AsyncEngine) -> int:
    """Delete expired unconsumed capabilities; consumed rows follow task retention."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                DELETE FROM adset_duplicate_previews
                WHERE expires_at <= clock_timestamp()
                  AND task_id IS NULL
                """
            )
        )
        return int(result.rowcount or 0)


async def delete_expired_browser_operation_capabilities(engine: AsyncEngine) -> int:
    """Delete grants only after their signed expiry can no longer be accepted."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                DELETE FROM browser_operation_capability_uses
                WHERE expires_at <= clock_timestamp()
                """
            )
        )
        return int(result.rowcount or 0)


async def delete_enable_recommendations(
    engine: AsyncEngine, policy: dict[str, str], *, now: datetime | None = None
) -> int:
    retention = policy.get("enable_recommendations", "30 days")
    if is_special(retention):
        return 0
    cutoff = cutoff_datetime(retention, now=now)
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM enable_recommendations WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
        return result.rowcount or 0


async def delete_expired_invites(
    engine: AsyncEngine, policy: dict[str, str], *, now: datetime | None = None
) -> int:
    retention = policy.get("telegram_invites_expired", "30 days")
    if is_special(retention):
        return 0
    cutoff = cutoff_datetime(retention, now=now)
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "DELETE FROM telegram_invites "
                "WHERE COALESCE(used_at, revoked_at, expires_at) < :cutoff"
            ),
            {"cutoff": cutoff},
        )
        return result.rowcount or 0


async def delete_old_operator_revision_events(
    engine: AsyncEngine, policy: dict[str, str], *, now: datetime | None = None
) -> int:
    """Delete old cursor events while always retaining the latest revision.

    Operator reconciliation only reads the monotonic maximum.  Keeping the
    current maximum makes an empty cursor impossible even when the system has
    been idle longer than the retention window.
    """
    retention = policy.get("operator_revision_events", "7 days")
    if is_special(retention):
        return 0
    cutoff = cutoff_datetime(retention, now=now)
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                WITH latest AS (
                    SELECT MAX(revision) AS revision
                    FROM operator_revision_events
                )
                DELETE FROM operator_revision_events AS event
                USING latest
                WHERE event.created_at < :cutoff
                  AND event.revision < latest.revision
                """
            ),
            {"cutoff": cutoff},
        )
        return result.rowcount or 0


async def delete_terminal_notification_control_plane(
    engine: AsyncEngine,
    policy: dict[str, str],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Bound durable Telegram history without crossing safety boundaries.

    Cleanup is deliberately terminal-state based.  It never removes active
    incidents, pending/retry/leased deliveries or updates, and notification
    events remain the dedupe authority until every delivery has been terminal
    for the configured audit window.
    """
    now = now or datetime.now(timezone.utc)
    keys = {
        "action_tokens": ("telegram_action_tokens_terminal", "90 days"),
        "navigation_tokens": ("telegram_navigation_tokens_terminal", "30 days"),
        "command_replies": ("telegram_command_replies_terminal", "90 days"),
        "updates": ("telegram_updates_terminal", "90 days"),
        "incidents": ("incidents_terminal", "365 days"),
        "events": ("notification_events_terminal", "365 days"),
    }
    cutoffs: dict[str, datetime | None] = {}
    for label, (policy_key, default) in keys.items():
        retention = policy.get(policy_key, default)
        cutoffs[label] = None if is_special(retention) else cutoff_datetime(retention, now=now)

    counts = {
        "telegram_action_tokens": 0,
        "telegram_navigation_tokens": 0,
        "telegram_command_replies": 0,
        "telegram_updates_inbox": 0,
        "incidents": 0,
        "notification_events": 0,
        "notification_deliveries": 0,
    }

    cutoff = cutoffs["action_tokens"]
    if cutoff is not None:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    DELETE FROM telegram_action_tokens
                    WHERE GREATEST(
                              expires_at,
                              COALESCE(consumed_at, '-infinity'::timestamptz),
                              COALESCE(revoked_at, '-infinity'::timestamptz)
                          ) < :cutoff
                    """
                ),
                {"cutoff": cutoff},
            )
            counts["telegram_action_tokens"] = int(result.rowcount or 0)

    cutoff = cutoffs["navigation_tokens"]
    if cutoff is not None:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    DELETE FROM telegram_navigation_tokens
                    WHERE GREATEST(
                              expires_at,
                              COALESCE(consumed_at, '-infinity'::timestamptz),
                              COALESCE(revoked_at, '-infinity'::timestamptz)
                          ) < :cutoff
                    """
                ),
                {"cutoff": cutoff},
            )
            counts["telegram_navigation_tokens"] = int(result.rowcount or 0)

    # Replies are children of inbox rows.  Remove terminal replies first, then
    # retire only inbox idempotency keys with no remaining reply work.
    cutoff = cutoffs["command_replies"]
    if cutoff is not None:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    DELETE FROM telegram_command_replies
                    WHERE state IN ('sent','dead','unknown')
                      AND completed_at < :cutoff
                    """
                ),
                {"cutoff": cutoff},
            )
            counts["telegram_command_replies"] = int(result.rowcount or 0)

    cutoff = cutoffs["updates"]
    if cutoff is not None:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    DELETE FROM telegram_updates_inbox AS inbox
                    WHERE inbox.state IN ('processed','dead')
                      AND inbox.processed_at < :cutoff
                      AND NOT EXISTS (
                          SELECT 1
                          FROM telegram_command_replies reply
                          WHERE reply.bot_generation = inbox.bot_generation
                            AND reply.update_id = inbox.update_id
                      )
                    """
                ),
                {"cutoff": cutoff},
            )
            counts["telegram_updates_inbox"] = int(result.rowcount or 0)

    # Deleting terminal incidents first removes their editable message slots
    # and detaches immutable events through ON DELETE SET NULL.  Active
    # incidents can therefore never lose their card/event audit trail.
    cutoff = cutoffs["incidents"]
    if cutoff is not None:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    DELETE FROM incidents
                    WHERE status IN ('resolved','failed')
                      AND COALESCE(resolved_at, updated_at) < :cutoff
                    """
                ),
                {"cutoff": cutoff},
            )
            counts["incidents"] = int(result.rowcount or 0)

    cutoff = cutoffs["events"]
    if cutoff is not None:
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        WITH doomed AS MATERIALIZED (
                            SELECT event.id
                            FROM notification_events event
                            WHERE event.incident_id IS NULL
                              AND event.created_at < :cutoff
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM telegram_message_slots slot
                                  WHERE slot.last_event_id = event.id
                              )
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM notification_deliveries delivery
                                  WHERE delivery.event_id = event.id
                                    AND (
                                        delivery.state NOT IN
                                            ('sent','dead','unknown','superseded')
                                        OR delivery.completed_at IS NULL
                                        OR delivery.completed_at >= :cutoff
                                    )
                              )
                        ), delivery_total AS (
                            SELECT COUNT(*)::bigint AS count
                            FROM notification_deliveries delivery
                            JOIN doomed ON doomed.id = delivery.event_id
                        ), deleted AS (
                            DELETE FROM notification_events event
                            USING doomed
                            WHERE event.id = doomed.id
                            RETURNING event.id
                        )
                        SELECT
                            (SELECT COUNT(*)::bigint FROM deleted) AS event_count,
                            (SELECT count FROM delivery_total) AS delivery_count
                        """
                    ),
                    {"cutoff": cutoff},
                )
            ).one()
            counts["notification_events"] = int(row.event_count or 0)
            counts["notification_deliveries"] = int(row.delivery_count or 0)

    return counts


async def write_audit(
    engine: AsyncEngine,
    *,
    started_at: datetime,
    finished_at: datetime,
    counts: dict[str, Any],
) -> None:
    """Запись audit в system_config.value под ключом cleanup_runs."""
    payload = {
        "last_run_started_at": started_at.isoformat(),
        "last_run_finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "counts": counts,
    }
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (:k, CAST(:v AS JSONB), 'Аудит запусков cleanup_worker')
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """
            ),
            {"k": "cleanup_runs", "v": json.dumps(payload)},
        )


async def run_once(engine: AsyncEngine) -> dict[str, Any]:
    """Один прогон cleanup-воркера. Возвращает summary."""
    started_at = datetime.now(timezone.utc)
    logger.info("=== Cleanup worker run started at %s ===", started_at.isoformat())

    policy = await load_policy(engine)
    counts: dict[str, Any] = {}

    try:
        counts["partitions_created"] = await create_next_partition_if_missing(engine)
    except Exception as exc:
        logger.exception("create_next_partition_if_missing failed: %s", exc)
        counts["partitions_created_error"] = str(exc)

    try:
        counts["partitions_dropped"] = await drop_old_partitions(engine, policy)
    except Exception as exc:
        logger.exception("drop_old_partitions failed: %s", exc)
        counts["partitions_dropped_error"] = str(exc)

    try:
        counts["task_queue_deleted"] = await delete_task_queue_completed(engine, policy)
    except Exception as exc:
        logger.exception("delete_task_queue_completed failed: %s", exc)
        counts["task_queue_deleted_error"] = str(exc)

    try:
        counts["adset_duplicate_previews_deleted"] = await delete_expired_adset_duplicate_previews(
            engine
        )
    except Exception as exc:
        logger.exception("delete_expired_adset_duplicate_previews failed: %s", exc)
        counts["adset_duplicate_previews_deleted_error"] = str(exc)

    try:
        counts["browser_operation_leases_deleted"] = await delete_expired_browser_operation_leases(
            engine
        )
    except Exception as exc:
        logger.exception("delete_expired_browser_operation_leases failed: %s", exc)
        counts["browser_operation_leases_deleted_error"] = str(exc)

    try:
        counts[
            "browser_operation_capabilities_deleted"
        ] = await delete_expired_browser_operation_capabilities(engine)
    except Exception as exc:
        logger.exception(
            "delete_expired_browser_operation_capabilities failed: %s",
            exc,
        )
        counts["browser_operation_capabilities_deleted_error"] = str(exc)

    try:
        counts["enable_recommendations_deleted"] = await delete_enable_recommendations(
            engine, policy
        )
    except Exception as exc:
        logger.exception("delete_enable_recommendations failed: %s", exc)
        counts["enable_recommendations_deleted_error"] = str(exc)

    try:
        counts["telegram_invites_deleted"] = await delete_expired_invites(engine, policy)
    except Exception as exc:
        logger.exception("delete_expired_invites failed: %s", exc)
        counts["telegram_invites_deleted_error"] = str(exc)

    try:
        counts["operator_revision_events_deleted"] = await delete_old_operator_revision_events(
            engine, policy
        )
    except Exception as exc:
        logger.exception("delete_old_operator_revision_events failed: %s", exc)
        counts["operator_revision_events_deleted_error"] = str(exc)

    try:
        counts[
            "notification_control_plane_deleted"
        ] = await delete_terminal_notification_control_plane(engine, policy)
    except Exception as exc:
        logger.exception("delete_terminal_notification_control_plane failed: %s", exc)
        counts["notification_control_plane_deleted_error"] = str(exc)

    try:
        counts["panel_auth_expired_deleted"] = await cleanup_expired_panel_auth_records(engine)
    except Exception as exc:
        logger.exception("cleanup_expired_panel_auth_records failed: %s", exc)
        counts["panel_auth_expired_deleted_error"] = str(exc)

    finished_at = datetime.now(timezone.utc)
    logger.info("=== Cleanup worker run finished at %s ===", finished_at.isoformat())
    logger.info("Counts: %s", counts)

    try:
        await write_audit(engine, started_at=started_at, finished_at=finished_at, counts=counts)
    except Exception as exc:
        logger.exception("write_audit failed: %s", exc)

    return counts
