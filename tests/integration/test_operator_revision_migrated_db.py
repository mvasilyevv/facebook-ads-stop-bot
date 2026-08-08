from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlparse

import asyncpg
import pytest


def _migrated_database_url() -> str:
    raw = os.environ.get("MIGRATED_TEST_DATABASE_URL", "")
    if not raw:
        pytest.skip("MIGRATED_TEST_DATABASE_URL is required for trigger acceptance")
    dsn = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
    database = urlparse(dsn).path.removeprefix("/")
    if "test" not in database:
        pytest.fail("operator revision acceptance requires an isolated test database")
    return dsn


@pytest.mark.asyncio
async def test_append_only_revision_has_no_cross_transaction_hot_row() -> None:
    """An unrelated trigger must not wait for another transaction's revision bump.

    With the former singleton UPDATE, transaction B blocked on the revision row
    held by A.  If A then needed B's incident row, PostgreSQL detected a
    lock-order deadlock.  Sequence-backed inserts let B finish its trigger while
    A remains open, so the business-row wait has no cycle.
    """

    dsn = _migrated_database_url()
    first = await asyncpg.connect(dsn)
    second = await asyncpg.connect(dsn)
    cleanup = await asyncpg.connect(dsn)
    suffix = uuid.uuid4().hex
    incident_id = uuid.uuid4()
    task_id: int | None = None
    try:
        task_id = await cleanup.fetchval(
            """
            INSERT INTO task_queue
                (task_type, status, idempotency_key, payload, requested_by,
                 lane, priority, available_at, deadline_at)
            VALUES
                ('meta_api_mutation', 'pending', $1,
                 '{"mutation_kind":"pause_ad","target_id":"revision-race","ad_account_id":"123"}'::jsonb,
                 'test',
                 'money', 100, clock_timestamp(),
                 clock_timestamp() + INTERVAL '30 seconds')
            RETURNING id
            """,
            f"operator-revision-race:{suffix}",
        )
        await cleanup.execute(
            """
            INSERT INTO incidents
                (id, incident_key, generation, resource_type, resource_id,
                 severity, status, title)
            VALUES ($1, $2, 1, 'test', $3, 'warning', 'open', 'Revision race')
            """,
            incident_id,
            f"operator-revision-race:{suffix}",
            suffix,
        )

        first_tx = first.transaction()
        second_tx = second.transaction()
        await first_tx.start()
        await first.execute(
            "UPDATE task_queue SET priority = priority + 1 WHERE id = $1",
            task_id,
        )

        await second_tx.start()
        # This is the decisive assertion: transaction A remains open, yet B's
        # independent trigger completes without waiting on a shared hot row.
        await asyncio.wait_for(
            second.execute(
                "UPDATE incidents SET summary = 'tx-b' WHERE id = $1",
                incident_id,
            ),
            timeout=1.0,
        )

        blocked_business_update = asyncio.create_task(
            first.execute(
                "UPDATE incidents SET summary = 'tx-a' WHERE id = $1",
                incident_id,
            )
        )
        await asyncio.sleep(0.05)
        assert not blocked_business_update.done()
        await second_tx.commit()
        await asyncio.wait_for(blocked_business_update, timeout=1.0)
        await first_tx.commit()

        scopes = await cleanup.fetch(
            """
            SELECT scope
            FROM operator_revision_events
            WHERE event_id IN ($1, $2)
            """,
            str(task_id),
            str(incident_id),
        )
        assert {str(row["scope"]) for row in scopes} >= {"task", "incident"}
    finally:
        if first.is_in_transaction():
            await first.execute("ROLLBACK")
        if second.is_in_transaction():
            await second.execute("ROLLBACK")
        if task_id is not None:
            await cleanup.execute("DELETE FROM task_queue WHERE id = $1", task_id)
        await cleanup.execute("DELETE FROM incidents WHERE id = $1", incident_id)
        await first.close()
        await second.close()
        await cleanup.close()


@pytest.mark.asyncio
async def test_commit_cursor_advances_for_late_lower_identity_commit() -> None:
    """A late commit is visible even when MAX(identity) cannot change."""

    dsn = _migrated_database_url()
    first = await asyncpg.connect(dsn)
    second = await asyncpg.connect(dsn)
    observer = await asyncpg.connect(dsn)

    async def cursor() -> int:
        return int(
            await observer.fetchval(
                """
                SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0'::pg_lsn)::bigint
                """
            )
        )

    first_event = f"late-first-{uuid.uuid4().hex}"
    second_event = f"early-second-{uuid.uuid4().hex}"
    first_tx = first.transaction()
    try:
        await first_tx.start()
        first_revision = await first.fetchval(
            """
            INSERT INTO operator_revision_events (scope, event_id)
            VALUES ('cursor-test', $1)
            RETURNING revision
            """,
            first_event,
        )
        second_revision = await second.fetchval(
            """
            INSERT INTO operator_revision_events (scope, event_id)
            VALUES ('cursor-test', $1)
            RETURNING revision
            """,
            second_event,
        )
        assert second_revision > first_revision

        max_before_late_commit = await observer.fetchval(
            "SELECT MAX(revision) FROM operator_revision_events"
        )
        cursor_before_late_commit = await cursor()
        await first_tx.commit()

        max_after_late_commit = await observer.fetchval(
            "SELECT MAX(revision) FROM operator_revision_events"
        )
        cursor_after_late_commit = await cursor()
        assert max_after_late_commit == max_before_late_commit
        assert cursor_after_late_commit > cursor_before_late_commit
    finally:
        if first.is_in_transaction():
            await first.execute("ROLLBACK")
        await observer.execute(
            "DELETE FROM operator_revision_events WHERE event_id = ANY($1::text[])",
            [first_event, second_event],
        )
        await first.close()
        await second.close()
        await observer.close()
