from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from apps.cleanup_worker import main as cleanup_main


@pytest.mark.asyncio
@pytest.mark.parametrize("run_cleanup_on_start", [False, True])
async def test_startup_prepares_partitions_before_optional_retention_run(
    monkeypatch: pytest.MonkeyPatch,
    run_cleanup_on_start: bool,
) -> None:
    calls: list[str] = []

    async def _create(_engine, *, fail_on_error: bool) -> dict[str, int]:
        assert fail_on_error is True
        calls.append("partitions")
        return {"ad_metrics": 2}

    async def _run_once(_engine) -> dict[str, int]:
        calls.append("retention")
        return {}

    monkeypatch.setattr(cleanup_main, "create_next_partition_if_missing", _create)
    monkeypatch.setattr(cleanup_main, "run_once", _run_once)

    await cleanup_main._initialize_partition_storage(
        object(),
        run_cleanup_on_start=run_cleanup_on_start,
    )

    assert calls == (["partitions", "retention"] if run_cleanup_on_start else ["partitions"])


@pytest.mark.asyncio
async def test_startup_fails_when_partition_preparation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _create(_engine, *, fail_on_error: bool) -> dict[str, int]:
        assert fail_on_error is True
        raise RuntimeError("partition DDL failed")

    monkeypatch.setattr(cleanup_main, "create_next_partition_if_missing", _create)

    with pytest.raises(RuntimeError, match="partition DDL failed"):
        await cleanup_main._initialize_partition_storage(
            object(),
            run_cleanup_on_start=False,
        )


@pytest.mark.asyncio
async def test_unhandled_cleanup_crash_opens_durable_warning(monkeypatch) -> None:
    async def _run_once(_engine):
        raise RuntimeError("boom")

    publish_health = AsyncMock(return_value=True)
    monkeypatch.setattr(cleanup_main, "run_once", _run_once)
    monkeypatch.setattr(cleanup_main, "publish_cleanup_run_health", publish_health)
    engine = object()

    with pytest.raises(RuntimeError, match="boom"):
        await cleanup_main._run_cleanup(engine)

    publish_health.assert_awaited_once_with(
        engine,
        success=False,
        error_count=1,
    )


def test_full_cleanup_creates_partitions_before_dropping_old_ones() -> None:
    source = Path(__file__).resolve().parents[2] / "apps/cleanup_worker/worker.py"
    run_once_source = source.read_text(encoding="utf-8").split("async def run_once", 1)[1]

    assert run_once_source.index("create_next_partition_if_missing") < run_once_source.index(
        "drop_old_partitions"
    )
