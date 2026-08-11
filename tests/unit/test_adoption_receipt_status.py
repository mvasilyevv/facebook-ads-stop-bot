from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/adoption-receipt-status.py"
SPEC = importlib.util.spec_from_file_location("adoption_receipt_status", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize(
    ("present", "expected"),
    [(True, 0), (False, 3)],
)
def test_status_exit_semantics(
    monkeypatch: pytest.MonkeyPatch,
    present: bool,
    expected: int,
) -> None:
    async def fake_status():
        return SimpleNamespace(imported_at=datetime(2026, 8, 9, tzinfo=UTC)) if present else None

    monkeypatch.setattr(MODULE, "_receipt_status", fake_status)

    assert MODULE.main() == expected


def test_status_database_error_is_exit_one(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_status():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(MODULE, "_receipt_status", fake_status)

    assert MODULE.main() == 1
