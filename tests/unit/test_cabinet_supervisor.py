"""Process-local contracts for durable per-cabinet progress classification."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from core.observer import cabinet_supervisor as supervisor_module
from core.observer.cabinet_supervisor import CabinetLease, CabinetSupervisor


class _ConnectionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Engine:
    def connect(self) -> _ConnectionContext:
        return _ConnectionContext()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "reported_error", "expected_snapshot", "expected_error"),
    [
        ("success", None, True, None),
        ("empty", "no_active_ads", True, None),
        ("error", "login_required", False, "login_required"),
        ("partial", "metrics_contract_revision:0", False, "metrics_contract_revision:0"),
        ("partial", "unclassified_empty_scan", False, "unclassified_empty_scan"),
    ],
)
async def test_actor_progress_persists_errors_only_for_degraded_results(
    monkeypatch,
    outcome: str,
    reported_error: str | None,
    expected_snapshot: bool,
    expected_error: str | None,
) -> None:
    account = "111"
    lease = CabinetLease(account, uuid.uuid4(), 1)
    progress_calls: list[dict[str, Any]] = []

    async def acquire(*_args: object, **_kwargs: object) -> CabinetLease:
        return lease

    async def update(*_args: object, **kwargs: Any) -> bool:
        progress_calls.append(kwargs)
        return True

    async def release(*_args: object, **_kwargs: object) -> bool:
        return True

    async def lock(*_args: object, **_kwargs: object) -> bool:
        return True

    async def unlock(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(supervisor_module, "acquire_cabinet_lease", acquire)
    monkeypatch.setattr(supervisor_module, "update_cabinet_progress", update)
    monkeypatch.setattr(supervisor_module, "release_cabinet_lease", release)
    monkeypatch.setattr(CabinetSupervisor, "_try_actor_lock", lock)
    monkeypatch.setattr(CabinetSupervisor, "_release_actor_lock", unlock)

    supervisor = CabinetSupervisor(
        _Engine(),  # type: ignore[arg-type]
        owner_instance=lease.owner_instance,
        scan_deadline_seconds=10,
        lease_ttl_seconds=20,
    )

    async def run_cabinet(
        account_id: str,
        _index: int,
        _lease: CabinetLease,
    ) -> dict[str, object]:
        return {
            "ad_account_id": account_id,
            "outcome": outcome,
            "error": reported_error,
        }

    result = await supervisor.run_cycle([account], run_cabinet)

    assert result[0]["outcome"] == outcome
    assert progress_calls[-1]["stage"] == "idle"
    assert progress_calls[-1]["has_snapshot"] is expected_snapshot
    assert progress_calls[-1]["error_code"] == expected_error
