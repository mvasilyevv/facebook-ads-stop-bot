# -*- coding: utf-8 -*-
"""Интеграционные проверки cleanup_worker на изолированной test-БД."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from apps.cleanup_worker.worker import (
    delete_expired_adset_duplicate_previews,
    delete_old_operator_revision_events,
    delete_task_queue_completed,
    load_policy,
)


@pytest.mark.asyncio
async def test_duplicate_preview_cleanup_keeps_consumed_until_task_retention(pg_engine) -> None:
    suffix = uuid.uuid4().hex
    unconsumed_digest = hashlib.sha256(f"unconsumed:{suffix}".encode()).digest()
    consumed_digest = hashlib.sha256(f"consumed:{suffix}".encode()).digest()
    task_key = f"cleanup-duplicate-preview:{suffix}"
    task_id: int | None = None
    try:
        async with pg_engine.begin() as conn:
            task_id = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO task_queue (
                            task_type, status, idempotency_key, payload,
                            requested_by, lane
                        )
                        VALUES (
                            'observer_scan', 'pending', :task_key,
                            '{}'::jsonb, 'test', 'interactive'
                        )
                        RETURNING id
                        """
                    ),
                    {"task_key": task_key},
                )
            ).scalar_one()
            await conn.execute(
                text(
                    """
                    INSERT INTO adset_duplicate_previews (
                        token_digest, principal, preview, task_payload,
                        plan_digest, idempotency_key, task_id,
                        created_at, expires_at, consumed_at
                    )
                    VALUES
                        (
                            :unconsumed, 'operator:web', '{}'::jsonb, '{}'::jsonb,
                            :plan_digest,
                            'meta:duplicate-adset:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                            NULL,
                            clock_timestamp() - INTERVAL '1 hour',
                            clock_timestamp() - INTERVAL '1 minute',
                            NULL
                        ),
                        (
                            :consumed, 'operator:web', '{}'::jsonb, '{}'::jsonb,
                            :plan_digest,
                            'meta:duplicate-adset:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                            :task_id,
                            clock_timestamp() - INTERVAL '1 hour',
                            clock_timestamp() - INTERVAL '1 minute',
                            clock_timestamp() - INTERVAL '30 minutes'
                        )
                    """
                ),
                {
                    "unconsumed": unconsumed_digest,
                    "consumed": consumed_digest,
                    "plan_digest": hashlib.sha256(suffix.encode()).digest(),
                    "task_id": task_id,
                },
            )

        deleted = await delete_expired_adset_duplicate_previews(pg_engine)
        assert deleted == 1
        async with pg_engine.connect() as conn:
            remaining = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM adset_duplicate_previews
                    WHERE token_digest = ANY(:digests)
                    """
                ),
                {"digests": [unconsumed_digest, consumed_digest]},
            )
        assert remaining == 1

        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        task_id = None
        async with pg_engine.connect() as conn:
            remaining = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM adset_duplicate_previews
                    WHERE token_digest = :consumed
                    """
                ),
                {"consumed": consumed_digest},
            )
        assert remaining == 0
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM adset_duplicate_previews WHERE token_digest = ANY(:digests)"),
                {"digests": [unconsumed_digest, consumed_digest]},
            )
            if task_id is not None:
                await conn.execute(
                    text("DELETE FROM task_queue WHERE id = :task_id"),
                    {"task_id": task_id},
                )


@pytest.mark.asyncio
async def test_operator_revision_cleanup_keeps_latest_cursor(pg_engine) -> None:
    now = datetime.now(timezone.utc)
    event_ids = [f"cleanup-revision-{uuid.uuid4().hex}" for _ in range(3)]
    try:
        async with pg_engine.begin() as conn:
            revisions = []
            for event_id, created_at in zip(
                event_ids,
                (now - timedelta(days=30), now - timedelta(days=20), now - timedelta(days=10)),
                strict=True,
            ):
                revision = (
                    await conn.execute(
                        text(
                            """
                            INSERT INTO operator_revision_events (scope, event_id, created_at)
                            VALUES ('cleanup-test', :event_id, :created_at)
                            RETURNING revision
                            """
                        ),
                        {"event_id": event_id, "created_at": created_at},
                    )
                ).scalar_one()
                revisions.append(revision)

        deleted = await delete_old_operator_revision_events(
            pg_engine,
            {"operator_revision_events": "7 days"},
            now=now,
        )
        assert deleted >= 2

        async with pg_engine.connect() as conn:
            remaining = (
                (
                    await conn.execute(
                        text(
                            """
                        SELECT revision
                        FROM operator_revision_events
                        WHERE event_id = ANY(:event_ids)
                        ORDER BY revision
                        """
                        ),
                        {"event_ids": event_ids},
                    )
                )
                .scalars()
                .all()
            )
        assert remaining == [revisions[-1]]
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM operator_revision_events WHERE event_id = ANY(:event_ids)"),
                {"event_ids": event_ids},
            )


# Проверяет load_policy — должен прочитать system_config.retention_policy
@pytest.mark.asyncio
async def test_load_policy_from_db(pg_engine) -> None:
    engine = pg_engine
    policy = await load_policy(engine)
    assert isinstance(policy, dict)
    assert "ad_metrics" in policy


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
                        (task_type, status, idempotency_key, payload, requested_by,
                         lane, completed_at, created_at, updated_at)
                    VALUES
                        ('observer_scan', 'succeeded', :k1,
                         CAST('{"source":"test"}' AS JSONB), 'test', 'interactive',
                         :c1, :c1, :c1),
                        ('observer_scan', 'succeeded', :k2,
                         CAST('{"source":"test"}' AS JSONB), 'test', 'interactive',
                         :c2, :c2, :c2)
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
