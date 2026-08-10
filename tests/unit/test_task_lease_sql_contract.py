from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _assert_every_owner_token_writer_checks_live_lease(relative: str) -> None:
    lines = (ROOT / relative).read_text(encoding="utf-8").splitlines()
    matches = 0
    for index, line in enumerate(lines):
        if "lease_owner = :lease_owner" not in line:
            continue
        window = "\n".join(lines[index : index + 5])
        if "lease_token = :lease_token" not in window:
            continue
        matches += 1
        assert "lease_expires_at > clock_timestamp()" in window, (
            f"{relative}:{index + 1} accepts owner/token after lease expiry"
        )
    assert matches > 0


def test_claimed_task_writers_require_unexpired_lease_authority() -> None:
    for relative in (
        "core/tasks/queue.py",
        "core/adset_pro/processing.py",
        "core/meta_api/freshness.py",
    ):
        _assert_every_owner_token_writer_checks_live_lease(relative)
