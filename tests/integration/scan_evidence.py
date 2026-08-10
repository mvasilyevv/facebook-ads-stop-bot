"""Helpers for tests that exercise the worker after the inner observer pipeline."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text


async def begin_complete_test_scan(engine, *, account_id: str) -> int:
    """Create the outer scan row that production creates before processing rows."""
    started_at = datetime.now(UTC)
    async with engine.begin() as conn:
        scan_id = await conn.scalar(
            text(
                """
                WITH next_id AS (SELECT nextval('scan_runs_id_seq') AS sid)
                INSERT INTO scan_runs (id, scan_id, started_at, ad_account_id)
                SELECT sid, sid, :started_at, :account_id FROM next_id
                RETURNING scan_id
                """
            ),
            {"started_at": started_at, "account_id": account_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO observer_config
                    (id, singleton_key, is_scanning_enabled)
                VALUES (gen_random_uuid(), 'default', TRUE)
                ON CONFLICT (singleton_key) DO UPDATE
                SET is_scanning_enabled = EXCLUDED.is_scanning_enabled
                """
            )
        )
    return int(scan_id)


async def finish_complete_test_scan(engine, *, scan_id: int, rows_total: int) -> None:
    """Publish the complete evidence that production writes after the pipeline."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE scan_runs
                SET finished_at = clock_timestamp(),
                    outcome = 'success',
                    rows_total = :rows_total,
                    error_message = NULL
                WHERE scan_id = :scan_id
                """
            ),
            {"scan_id": scan_id, "rows_total": rows_total},
        )


__all__ = ["begin_complete_test_scan", "finish_complete_test_scan"]
