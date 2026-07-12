# -*- coding: utf-8 -*-
"""Интеграционный тест cleanup_worker — реальная БД, скоупом одного pytest-mark.

Скип если нет POSTGRES_TEST_DB — иначе использует основную БД (минимально-инвазивно).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from apps.cleanup_worker.worker import (
    cleanup_orphan_media_files,
    delete_task_queue_completed,
    load_policy,
)


# Проверяет load_policy — должен прочитать system_config.retention_policy
@pytest.mark.asyncio
async def test_load_policy_from_db(pg_engine) -> None:
    engine = pg_engine
    policy = await load_policy(engine)
    assert isinstance(policy, dict)
    assert "ad_metrics" in policy or "ad_library_scan" in policy


# Проверяет что delete_task_queue_completed удаляет только просроченные succeeded
@pytest.mark.asyncio
async def test_delete_task_queue_succeeded(pg_engine) -> None:
    engine = pg_engine
    old_key = f"test_cleanup_old_{uuid.uuid4().hex[:8]}"
    fresh_key = f"test_cleanup_fresh_{uuid.uuid4().hex[:8]}"
    try:
        async with engine.begin() as conn:
            # Вставляем 2 fake task'а: один старый succeeded (40 дней назад), один свежий
            old_completed = datetime.now(timezone.utc) - timedelta(days=40)
            fresh_completed = datetime.now(timezone.utc) - timedelta(days=5)

            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by, completed_at, created_at, updated_at)
                    VALUES
                        ('disable', 'succeeded', :k1, CAST('{}' AS JSONB), 'test', :c1, :c1, :c1),
                        ('disable', 'succeeded', :k2, CAST('{}' AS JSONB), 'test', :c2, :c2, :c2)
                    """
                ),
                {"k1": old_key, "c1": old_completed, "k2": fresh_key, "c2": fresh_completed},
            )

        # Запускаем cleanup с дефолтным retention (30 days для succeeded)
        deleted = await delete_task_queue_completed(engine, {"task_queue_completed": "30 days"})
        assert deleted >= 1  # минимум наш old удалён

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT 1 FROM task_queue WHERE idempotency_key = :k"),
                    {"k": fresh_key},
                )
            ).first()
            assert row is not None, "fresh task не должен быть удалён"
            row = (
                await conn.execute(
                    text("SELECT 1 FROM task_queue WHERE idempotency_key = :k"),
                    {"k": old_key},
                )
            ).first()
            assert row is None, "old task должен быть удалён"
    finally:
        # Cleanup: удаляем оба ключа для чистоты (engine закроет pg_engine-фикстура)
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE idempotency_key IN (:k1, :k2)"),
                {"k1": fresh_key, "k2": old_key},
            )


# Проверяет FS-cleanup: orphan файл удаляется, не-orphan остаётся
def test_cleanup_orphan_media_files(tmp_path: Path) -> None:
    media_root = tmp_path / "ad_library_media"
    country_dir = media_root / "KE" / "999"
    country_dir.mkdir(parents=True)
    orphan = country_dir / "orphan.mp4"
    known = country_dir / "known.mp4"
    orphan.write_bytes(b"orphan")
    known.write_bytes(b"known")

    db_paths = {str(known)}
    deleted = cleanup_orphan_media_files(media_root, db_paths)

    assert deleted == 1
    assert not orphan.exists()
    assert known.exists()


# M-5 (аудит 2026-07-12): CREATE месячной партиции восстанавливается через detach,
# если строки уже попали в _default (иначе constraint-violation обрывал весь цикл).
# Используем изолированную партиционированную таблицу — основную схему не трогаем.
@pytest.mark.asyncio
async def test_create_month_partition_recovers_from_default(pg_engine) -> None:
    from apps.cleanup_worker.worker import _create_month_partition

    suffix = uuid.uuid4().hex[:8]
    parent = f"t_part_{suffix}"
    default_name = f"{parent}_default"
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(f"CREATE TABLE {parent} (id int, ts timestamptz) PARTITION BY RANGE (ts)")
            )
            await conn.execute(text(f"CREATE TABLE {default_name} PARTITION OF {parent} DEFAULT"))
            # Строка мая-2026 уходит в default (месячной партиции ещё нет).
            await conn.execute(
                text(f"INSERT INTO {parent} (id, ts) VALUES (1, '2026-05-15T10:00:00+00')")
            )

        # Прямой CREATE упал бы (default содержит строку диапазона) → detach-recovery.
        created = await _create_month_partition(pg_engine, parent, "ts", "2026-05-01", "2026-06-01")
        assert created is True

        async with pg_engine.connect() as conn:
            # Партиция мая существует и строка переехала в неё из default.
            part_exists = (
                await conn.execute(text(f"SELECT to_regclass('{parent}_2026_05')"))
            ).scalar()
            assert part_exists is not None
            in_month = (await conn.execute(text(f"SELECT COUNT(*) FROM {parent}_2026_05"))).scalar()
            in_default = (await conn.execute(text(f"SELECT COUNT(*) FROM {default_name}"))).scalar()
        assert in_month == 1, "строка должна переехать из default в месячную партицию"
        assert in_default == 0, "default не должен держать строку майского диапазона"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {parent} CASCADE"))


# Повторный вызов для существующей партиции — no-op (False), не падает.
@pytest.mark.asyncio
async def test_create_month_partition_idempotent(pg_engine) -> None:
    from apps.cleanup_worker.worker import _create_month_partition

    suffix = uuid.uuid4().hex[:8]
    parent = f"t_part2_{suffix}"
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(f"CREATE TABLE {parent} (id int, ts timestamptz) PARTITION BY RANGE (ts)")
            )
            await conn.execute(text(f"CREATE TABLE {parent}_default PARTITION OF {parent} DEFAULT"))
        first = await _create_month_partition(pg_engine, parent, "ts", "2026-05-01", "2026-06-01")
        second = await _create_month_partition(pg_engine, parent, "ts", "2026-05-01", "2026-06-01")
        assert first is True
        assert second is False
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {parent} CASCADE"))
