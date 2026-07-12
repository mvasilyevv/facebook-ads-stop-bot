# -*- coding: utf-8 -*-
"""Логика одного прогона cleanup-воркера.

Один прогон = одна транзакция на каждую операцию (независимость операций друг от друга).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.cleanup_worker.retention import (
    cutoff_datetime,
    get_default_policy,
    is_special,
)

logger = logging.getLogger(__name__)

# 8 партиционированных таблиц + столбец-партиция + ключ в retention_policy
_PARTITIONED: list[tuple[str, str, str]] = [
    ("ad_metrics", "cycle_ts", "ad_metrics"),
    ("alert_events", "created_at", "alert_events"),
    ("scan_runs", "started_at", "scan_runs"),
    ("meta_api_audit_log", "created_at", "meta_api_audit_log"),
    ("meta_api_webhook_event", "received_at", "meta_api_webhook_event"),
    ("ad_library_snapshot", "scanned_at", "ad_library_snapshot"),
    ("tracker_postback", "received_at", "tracker_postback"),
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
    engine: AsyncEngine, *, now: datetime | None = None
) -> dict[str, int]:
    """Создаёт партиции на текущий + следующий месяц для всех партиционированных таблиц.

    Каждая таблица обрабатывается изолированно: ошибка на одной (напр. неожиданный
    constraint) логируется и НЕ обрывает создание партиций для остальных (M-5).
    """
    now = now or datetime.now(timezone.utc)
    created: dict[str, int] = {}

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
        created[table] = count_created
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

    # Draft cleanup — 24 часа
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "DELETE FROM task_queue "
                "WHERE status = 'draft' AND created_at < NOW() - INTERVAL '24 hours'"
            )
        )
        deleted_total += result.rowcount or 0

    return deleted_total


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


async def delete_old_cabinet_archives(
    engine: AsyncEngine, policy: dict[str, str], *, now: datetime | None = None
) -> int:
    retention = policy.get("cabinet_day_archives", "365 days")
    if is_special(retention):
        return 0
    cutoff = cutoff_datetime(retention, now=now)
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM cabinet_day_archives WHERE started_at < :cutoff"),
            {"cutoff": cutoff},
        )
        return result.rowcount or 0


async def delete_old_ad_library_scans(
    engine: AsyncEngine, policy: dict[str, str], *, now: datetime | None = None
) -> int:
    retention = policy.get("ad_library_scan", "14 days")
    if is_special(retention):
        return 0
    cutoff = cutoff_datetime(retention, now=now)
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM ad_library_scan WHERE started_at < :cutoff"),
            {"cutoff": cutoff},
        )
        return result.rowcount or 0


async def delete_orphan_ad_library_ads(
    engine: AsyncEngine, policy: dict[str, str], *, now: datetime | None = None
) -> int:
    """Удаляет ad_library_ad без свежих snapshot'ов и не в winner_archive."""
    retention = policy.get("ad_library_ad_orphan", "14 days")
    if is_special(retention):
        return 0
    cutoff = cutoff_datetime(retention, now=now)
    async with engine.begin() as conn:
        # Сначала собираем кандидатов чтобы потом удалить связанные media files
        result = await conn.execute(
            text(
                """
                DELETE FROM ad_library_ad a
                WHERE NOT EXISTS (
                    SELECT 1 FROM ad_library_snapshot s
                    WHERE s.ad_archive_id = a.ad_archive_id
                      AND s.scanned_at > :cutoff
                )
                AND NOT EXISTS (
                    SELECT 1 FROM ad_library_winner_archive w
                    WHERE w.ad_archive_id = a.ad_archive_id
                )
                """
            ),
            {"cutoff": cutoff},
        )
        return result.rowcount or 0


def cleanup_orphan_media_files(media_root: Path, db_paths: set[str]) -> int:
    """Удаляет файлы с диска которых нет в БД (FS-scan).

    Это синхронная функция (filesystem I/O) — вызывается в потоке.
    Возвращает количество удалённых файлов.
    """
    if not media_root.exists():
        return 0
    deleted = 0
    for f in media_root.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f)
        # Все варианты пути в БД — абсолютные и относительные
        if rel not in db_paths and str(f.relative_to(media_root.parent)) not in db_paths:
            try:
                f.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("Не смог удалить %s: %s", f, exc)
    return deleted


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


async def run_once(engine: AsyncEngine, *, media_root: Path | None = None) -> dict[str, Any]:
    """Один прогон cleanup-воркера. Возвращает summary."""
    started_at = datetime.now(timezone.utc)
    logger.info("=== Cleanup worker run started at %s ===", started_at.isoformat())

    policy = await load_policy(engine)
    counts: dict[str, Any] = {}

    try:
        counts["partitions_dropped"] = await drop_old_partitions(engine, policy)
    except Exception as exc:
        logger.exception("drop_old_partitions failed: %s", exc)
        counts["partitions_dropped_error"] = str(exc)

    try:
        counts["partitions_created"] = await create_next_partition_if_missing(engine)
    except Exception as exc:
        logger.exception("create_next_partition_if_missing failed: %s", exc)
        counts["partitions_created_error"] = str(exc)

    try:
        counts["task_queue_deleted"] = await delete_task_queue_completed(engine, policy)
    except Exception as exc:
        logger.exception("delete_task_queue_completed failed: %s", exc)
        counts["task_queue_deleted_error"] = str(exc)

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
        counts["cabinet_archives_deleted"] = await delete_old_cabinet_archives(engine, policy)
    except Exception as exc:
        logger.exception("delete_old_cabinet_archives failed: %s", exc)
        counts["cabinet_archives_deleted_error"] = str(exc)

    try:
        counts["ad_library_scans_deleted"] = await delete_old_ad_library_scans(engine, policy)
    except Exception as exc:
        logger.exception("delete_old_ad_library_scans failed: %s", exc)
        counts["ad_library_scans_deleted_error"] = str(exc)

    try:
        counts["ad_library_orphan_ads_deleted"] = await delete_orphan_ad_library_ads(engine, policy)
    except Exception as exc:
        logger.exception("delete_orphan_ad_library_ads failed: %s", exc)
        counts["ad_library_orphan_ads_deleted_error"] = str(exc)

    # Filesystem cleanup для медиа
    # ruff ASYNC240: pathlib.exists() блокирующий, но вызывается раз в сутки
    # на boot'е cleanup_worker — приемлемо
    if media_root is not None and media_root.exists():  # noqa: ASYNC240
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT local_path FROM ad_library_media"))
                db_paths = {row[0] for row in result}
            # Запуск sync функции в executor
            import asyncio

            loop = asyncio.get_running_loop()
            deleted = await loop.run_in_executor(
                None, cleanup_orphan_media_files, media_root, db_paths
            )
            counts["media_files_deleted"] = deleted
        except Exception as exc:
            logger.exception("cleanup_orphan_media_files failed: %s", exc)
            counts["media_files_deleted_error"] = str(exc)

    finished_at = datetime.now(timezone.utc)
    logger.info("=== Cleanup worker run finished at %s ===", finished_at.isoformat())
    logger.info("Counts: %s", counts)

    try:
        await write_audit(engine, started_at=started_at, finished_at=finished_at, counts=counts)
    except Exception as exc:
        logger.exception("write_audit failed: %s", exc)

    return counts
