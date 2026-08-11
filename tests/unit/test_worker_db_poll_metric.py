from __future__ import annotations

from prometheus_client import generate_latest

from core import worker_metrics


def test_mark_worker_db_poll_success_records_current_timestamp(monkeypatch) -> None:
    monkeypatch.setattr(worker_metrics.time, "time", lambda: 1234.5)

    worker_metrics.mark_worker_db_poll_success("test-db-poll")

    exposition = generate_latest(worker_metrics.WORKER_DB_POLL_SUCCESS).decode()
    assert (
        'fb_agent_worker_db_poll_success_timestamp_seconds{worker="test-db-poll"} 1234.5'
        in exposition
    )
