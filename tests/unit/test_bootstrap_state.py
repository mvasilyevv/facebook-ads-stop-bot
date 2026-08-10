from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = ROOT / "scripts/bootstrap-state.py"
    spec = importlib.util.spec_from_file_location("bootstrap_state", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def test_owned_bootstrap_state_is_idempotent_across_release_ids(tmp_path: Path) -> None:
    state = tmp_path / "bootstrap-state.json"
    arguments = {
        "cluster_id": "a" * 32,
        "postgres_volume": "fb_agent_safety_first_pgdata",
        "platform_network": "fb_agent_safety_first_platform",
        "owner_uid": os.getuid(),
    }

    assert MODULE.record_owned(state, **arguments) is True
    original = state.read_bytes()
    assert MODULE.record_owned(state, **arguments) is False
    assert MODULE.validate_owned(state, **arguments) is True
    assert state.read_bytes() == original
    assert state.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".bootstrap-state.json.*")) == []


def test_owned_bootstrap_state_rejects_different_cluster_or_volume(
    tmp_path: Path,
) -> None:
    state = tmp_path / "bootstrap-state.json"
    owner_uid = os.getuid()
    MODULE.record_owned(
        state,
        cluster_id="a" * 32,
        postgres_volume="fb_agent_safety_first_pgdata",
        platform_network="fb_agent_safety_first_platform",
        owner_uid=owner_uid,
    )

    with pytest.raises(ValueError, match="different cluster or resource set"):
        MODULE.validate_owned(
            state,
            cluster_id="b" * 32,
            postgres_volume="fb_agent_safety_first_pgdata",
            platform_network="fb_agent_safety_first_platform",
            owner_uid=owner_uid,
        )


def test_unowned_state_is_absent_not_implicitly_adopted(tmp_path: Path) -> None:
    assert (
        MODULE.validate_owned(
            tmp_path / "bootstrap-state.json",
            cluster_id="a" * 32,
            postgres_volume="fb_agent_safety_first_pgdata",
            platform_network="fb_agent_safety_first_platform",
            owner_uid=os.getuid(),
        )
        is False
    )
