from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from apps.cleanup_worker import storage
from core import metrics


def _snapshot(*relations: storage.RelationSize) -> storage.DatabaseStorageSnapshot:
    return storage.DatabaseStorageSnapshot(
        database_size_bytes=8 * 1024**3,
        relations=relations,
    )


@pytest.mark.asyncio
async def test_database_snapshot_includes_table_partitions_and_total_size() -> None:
    class _Result:
        def __init__(self, *, scalar: int | None = None, rows: list[dict] | None = None):
            self._scalar = scalar
            self._rows = rows

        def scalar_one(self):
            return self._scalar

        def mappings(self):
            return self

        def all(self):
            return self._rows

    class _Connection:
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "pg_database_size" in sql:
                return _Result(scalar=1234)
            assert params is not None
            assert "ad_metrics" in params["tables"]
            assert "pg_total_relation_size" in sql
            return _Result(
                rows=[
                    {
                        "table_name": "ad_metrics",
                        "relation_name": "ad_metrics",
                        "kind": "table",
                        "size_bytes": 1000,
                    },
                    {
                        "table_name": "ad_metrics",
                        "relation_name": "ad_metrics_2026_07",
                        "kind": "partition",
                        "size_bytes": 700,
                    },
                    {
                        "table_name": "ad_metrics",
                        "relation_name": "ad_metrics_default",
                        "kind": "partition",
                        "size_bytes": 300,
                    },
                ]
            )

    class _Connect:
        async def __aenter__(self):
            return _Connection()

        async def __aexit__(self, *_args):
            return False

    class _Engine:
        def connect(self):
            return _Connect()

    snapshot = await storage.collect_database_storage(_Engine())

    assert snapshot.database_size_bytes == 1234
    assert [(row.relation_name, row.size_bytes) for row in snapshot.relations] == [
        ("ad_metrics", 1000),
        ("ad_metrics_2026_07", 700),
        ("ad_metrics_default", 300),
    ]
    monthly, default = snapshot.relations[1:]
    assert monthly.partition_started_at == datetime(2026, 7, 1, tzinfo=UTC)
    assert monthly.partition_ends_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert default.partition_started_at is None


@pytest.mark.asyncio
async def test_expired_partition_opens_one_human_warning(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    resolve = AsyncMock(return_value=False)
    monkeypatch.setattr(storage, "notify_recurring_incident", notify)
    monkeypatch.setattr(storage, "resolve_recurring_incident", resolve)
    now = datetime(2026, 8, 15, 12, tzinfo=UTC)
    snapshot = _snapshot(
        storage.RelationSize(
            table_name="ad_metrics",
            relation_name="ad_metrics_2026_05",
            kind="partition",
            size_bytes=4 * 1024**3,
            partition_started_at=datetime(2026, 5, 1, tzinfo=UTC),
            partition_ends_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )

    await storage.publish_retention_health(
        object(),
        snapshot=snapshot,
        policy={"ad_metrics": "45 days"},
        now=now,
    )

    notify.assert_awaited_once()
    facts = notify.await_args.kwargs
    assert facts["incident_key"] == storage.RETENTION_LAG_INCIDENT_KEY
    assert facts["severity"] == "warning"
    assert "просроч" in facts["summary"].lower()
    assert "что делать" in " ".join(facts["lines"]).lower()
    assert "ad_metrics" not in " ".join([facts["title"], facts["summary"], *facts["lines"]])
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_retention_observation_reuses_incident_key(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(storage, "notify_recurring_incident", notify)
    monkeypatch.setattr(storage, "resolve_recurring_incident", AsyncMock(return_value=False))
    now = datetime(2026, 8, 15, 12, tzinfo=UTC)
    snapshot = _snapshot(
        storage.RelationSize(
            table_name="scan_runs",
            relation_name="scan_runs_2026_05",
            kind="partition",
            size_bytes=1,
            partition_started_at=datetime(2026, 5, 1, tzinfo=UTC),
            partition_ends_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )

    for _ in range(2):
        await storage.publish_retention_health(
            object(),
            snapshot=snapshot,
            policy={"scan_runs": "30 days"},
            now=now,
        )

    assert notify.await_count == 2
    assert {call.kwargs["incident_key"] for call in notify.await_args_list} == {
        storage.RETENTION_LAG_INCIDENT_KEY
    }


def test_zero_cleanup_result_is_distinct_from_never_run() -> None:
    metrics.CLEANUP_ROWS_DELETED.clear()
    metrics.CLEANUP_PARTITIONS_DROPPED.clear()
    metrics.CLEANUP_LAST_RUN_FINISHED_TIMESTAMP.set(0)

    before = list(metrics.CLEANUP_ROWS_DELETED.collect())[0].samples
    assert before == []
    assert list(metrics.CLEANUP_LAST_RUN_FINISHED_TIMESTAMP.collect())[0].samples[0].value == 0

    finished_at = datetime(2026, 8, 15, 4, 0, tzinfo=UTC)
    metrics.record_cleanup_run(
        finished_at=finished_at,
        success=True,
        rows_deleted={"task_queue": 0},
        partitions_dropped={"ad_metrics": 0},
    )

    row_samples = list(metrics.CLEANUP_ROWS_DELETED.collect())[0].samples
    assert any(
        sample.labels == {"target": "task_queue"} and sample.value == 0 for sample in row_samples
    )
    assert (
        list(metrics.CLEANUP_LAST_RUN_FINISHED_TIMESTAMP.collect())[0].samples[0].value
        == finished_at.timestamp()
    )


@pytest.mark.asyncio
async def test_missing_cleanup_audit_opens_warning_instead_of_reporting_zero(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(storage, "load_last_cleanup_run", AsyncMock(return_value=None))
    monkeypatch.setattr(storage, "notify_recurring_incident", notify)
    monkeypatch.setattr(storage, "resolve_recurring_incident", AsyncMock(return_value=False))

    await storage.publish_cleanup_freshness(
        object(),
        now=datetime(2026, 8, 15, 12, tzinfo=UTC),
    )

    facts = notify.await_args.kwargs
    assert facts["incident_key"] == storage.CLEANUP_STALE_INCIDENT_KEY
    assert facts["severity"] == "warning"
    assert "нет подтверждённого" in facts["summary"].lower()


@pytest.mark.asyncio
async def test_failed_cleanup_audit_keeps_run_failure_incident_open(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(
        storage,
        "load_last_cleanup_run",
        AsyncMock(
            return_value=storage.CleanupRunAudit(
                finished_at=datetime(2026, 8, 15, 4, tzinfo=UTC),
                outcome="failed",
                error_count=2,
            )
        ),
    )
    monkeypatch.setattr(storage, "notify_recurring_incident", notify)
    monkeypatch.setattr(storage, "resolve_recurring_incident", AsyncMock(return_value=False))

    await storage.publish_cleanup_freshness(
        object(),
        now=datetime(2026, 8, 15, 12, tzinfo=UTC),
    )

    facts = notify.await_args.kwargs
    assert facts["incident_key"] == storage.CLEANUP_RUN_INCIDENT_KEY
    assert facts["severity"] == "warning"
    assert "2 ошибки" in facts["summary"]


@pytest.mark.asyncio
async def test_low_disk_incident_is_critical_and_reports_free_space(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(storage, "notify_recurring_incident", notify)
    monkeypatch.setattr(storage, "resolve_recurring_incident", AsyncMock(return_value=False))
    disk = storage.DiskSpace(
        path="/",
        total_bytes=100 * 1024**3,
        free_bytes=5 * 1024**3,
    )

    await storage.publish_disk_health(
        object(),
        disk=disk,
        min_free_bytes=10 * 1024**3,
        min_free_ratio=0.10,
    )

    facts = notify.await_args.kwargs
    assert facts["incident_key"] == storage.DISK_SPACE_INCIDENT_KEY
    assert facts["severity"] == "critical"
    assert "5 ГиБ" in facts["summary"]
    assert "что делать" in " ".join(facts["lines"]).lower()


@pytest.mark.asyncio
async def test_unavailable_disk_check_fails_closed_with_same_critical_incident(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(storage, "notify_recurring_incident", notify)
    metrics.DATABASE_DISK_CHECK_SUCCESS.set(1)

    await storage.publish_disk_check_unavailable(object())

    facts = notify.await_args.kwargs
    assert facts["incident_key"] == storage.DISK_SPACE_INCIDENT_KEY
    assert facts["severity"] == "critical"
    assert "неизвестно" in facts["summary"].lower()
    assert list(metrics.DATABASE_DISK_CHECK_SUCCESS.collect())[0].samples[0].value == 0
