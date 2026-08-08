"""Fail-closed architecture contract for the dedicated money worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.meta_api_worker.main import _validate_worker_lane_contract

ROOT = Path(__file__).resolve().parents[2]


def test_only_autopause_can_claim_exactly_money() -> None:
    _validate_worker_lane_contract("autopause", ("money",))

    with pytest.raises(RuntimeError, match="exactly the money lane"):
        _validate_worker_lane_contract("autopause", ("money", "interactive"))
    with pytest.raises(RuntimeError, match="only the autopause"):
        _validate_worker_lane_contract("meta_api", ("interactive", "money"))
    with pytest.raises(RuntimeError, match="invalid Meta worker lane"):
        _validate_worker_lane_contract("meta_api", ())
    with pytest.raises(RuntimeError, match="invalid Meta worker lane"):
        _validate_worker_lane_contract("meta_api", ("interactive", "unexpected"))


def test_compose_and_wrapper_preserve_money_worker_identity() -> None:
    compose = (ROOT / "deploy/compose/docker-compose.app.yml").read_text(encoding="utf-8")
    wrapper = (ROOT / "apps/autopause_worker/main.py").read_text(encoding="utf-8")

    autopause_block = compose.split("  autopause_worker:", 1)[1].split("\n  meta_api:", 1)[0]
    normal_block = compose.split("  meta_api:", 1)[1].split("\n  telegram_delivery_worker:", 1)[0]
    assert "META_API_WORKER_NAME: autopause" in autopause_block
    assert "META_API_WORKER_LANES: money" in autopause_block
    assert "META_API_WORKER_NAME: meta_api" in normal_block
    assert "META_API_WORKER_LANES: interactive,bulk,background" in normal_block
    assert 'setdefault("META_API_WORKER_NAME", "autopause")' in wrapper
    assert 'setdefault("META_API_WORKER_LANES", "money")' in wrapper
