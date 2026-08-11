from __future__ import annotations

import re

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from migrations.baseline_contract import (
    BASELINE_ARTIFACT_HASHES,
    CATALOG_ARTIFACTS_SQL,
    assert_catalog_artifacts,
)
from migrations.revision_guard import load_project_revision_chain

pytestmark = pytest.mark.asyncio


async def _assert_exact_contract(connection: AsyncConnection) -> None:
    rows = (await connection.execute(text(CATALOG_ARTIFACTS_SQL))).mappings()
    assert_catalog_artifacts(rows)


async def test_fresh_postgresql_baseline_has_exact_catalog_contract(
    pg_engine: AsyncEngine,
) -> None:
    async with pg_engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
        rows = list((await connection.execute(text(CATALOG_ARTIFACTS_SQL))).mappings())

    assert revision == load_project_revision_chain().head
    assert len(rows) == len(BASELINE_ARTIFACT_HASHES)
    assert_catalog_artifacts(rows)


@pytest.mark.parametrize(
    ("case", "ddl", "expected_drift"),
    [
        (
            "drop trigger",
            """
            DROP TRIGGER trg_task_queue_operator_notify
            ON public.task_queue
            """,
            "missing trigger:public.task_queue.trg_task_queue_operator_notify",
        ),
        (
            "drop function",
            """
            DROP FUNCTION public.notify_fb_operator_statement() CASCADE
            """,
            "missing function:public.notify_fb_operator_statement()",
        ),
        (
            "drop view",
            "DROP VIEW public.operator_revision_state",
            "missing view:public.operator_revision_state",
        ),
        (
            "disable trigger",
            """
            ALTER TABLE public.task_queue
            DISABLE TRIGGER trg_task_queue_operator_notify
            """,
            "definition changed trigger:public.task_queue.trg_task_queue_operator_notify",
        ),
        (
            "replace function body",
            """
            CREATE OR REPLACE FUNCTION public.notify_fb_operator_statement()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path TO 'pg_catalog', 'public'
            AS $changed$
            BEGIN
                RETURN NULL;
            END;
            $changed$
            """,
            "definition changed function:public.notify_fb_operator_statement()",
        ),
        (
            "replace view definition",
            """
            CREATE OR REPLACE VIEW public.operator_revision_state AS
            SELECT
                'operator'::varchar(16) AS singleton_key,
                COALESCE(max(revision), 0::bigint) AS revision,
                COALESCE(max(created_at), clock_timestamp()) AS updated_at
            FROM public.operator_revision_events
            WHERE revision >= 0
            """,
            "definition changed view:public.operator_revision_state",
        ),
        (
            "drop check constraint",
            """
            ALTER TABLE public.task_queue
            DROP CONSTRAINT ck_task_queue_lane
            """,
            "missing check_constraint:public.task_queue.ck_task_queue_lane",
        ),
        (
            "replace check constraint",
            """
            ALTER TABLE public.task_queue
            DROP CONSTRAINT ck_task_queue_lane,
            ADD CONSTRAINT ck_task_queue_lane
            CHECK (lane IN ('money', 'interactive', 'bulk'))
            """,
            "definition changed check_constraint:public.task_queue.ck_task_queue_lane",
        ),
        (
            "add unexpected check constraint",
            """
            ALTER TABLE public.task_queue
            ADD CONSTRAINT ck_task_queue_unreviewed
            CHECK (priority >= -2147483648)
            """,
            "unexpected check_constraint:public.task_queue.ck_task_queue_unreviewed",
        ),
    ],
    ids=[
        "drop-trigger",
        "drop-function",
        "drop-view",
        "disable-trigger",
        "replace-function",
        "replace-view",
        "drop-check",
        "replace-check",
        "unexpected-check",
    ],
)
async def test_catalog_guard_rejects_dropped_or_altered_runtime_artifact(
    pg_engine: AsyncEngine,
    case: str,
    ddl: str,
    expected_drift: str,
) -> None:
    del case
    async with pg_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _assert_exact_contract(connection)
            await connection.execute(text(ddl))

            with pytest.raises(RuntimeError, match=re.escape(expected_drift)):
                await _assert_exact_contract(connection)
        finally:
            await transaction.rollback()
