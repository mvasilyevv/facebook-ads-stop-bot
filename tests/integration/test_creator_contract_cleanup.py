"""Acceptance checks for the retired Vision creator contract cleanup."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


async def test_creator_storage_and_n_minus_one_trigger_are_absent(
    pg_engine: AsyncEngine,
) -> None:
    async with pg_engine.connect() as conn:
        table = (
            await conn.execute(text("SELECT to_regclass('public.creator_plans')"))
        ).scalar_one()
        trigger = (
            await conn.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_trigger
                    WHERE tgname = 'trg_task_queue_n_minus_one_defaults'
                      AND NOT tgisinternal
                    """
                )
            )
        ).scalar_one_or_none()
        function = (
            await conn.execute(
                text("SELECT to_regprocedure('public.task_queue_n_minus_one_defaults()')")
            )
        ).scalar_one()
        telegram_legacy_columns = set(
            (
                await conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'telegram_config'
                          AND column_name IN ('chat_id', 'poller_offset', 'poller_heartbeat_at')
                        """
                    )
                )
            ).scalars()
        )

    assert table is None
    assert trigger is None
    assert function is None
    assert telegram_legacy_columns == set()


async def test_plan_run_discriminator_is_rejected(pg_engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue (
                        task_type, status, idempotency_key, payload, requested_by,
                        lane, priority, available_at, lease_token, correlation_id
                    ) VALUES (
                        'plan_run', 'pending', 'retired-plan-run-contract-test',
                        '{}'::jsonb, 'test', 'bulk', 20, now(), 0,
                        gen_random_uuid()
                    )
                    """
                )
            )
