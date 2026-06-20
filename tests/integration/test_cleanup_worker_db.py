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
