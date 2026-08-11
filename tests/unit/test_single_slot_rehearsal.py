from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from core.adoption.bundle import parse_adoption_bundle_json
from tests.rehearsal.single_slot import (
    RehearsalError,
    _assert_tree_ownership,
    _require_ci_acknowledgement,
    _write_profile_seed,
    build_adoption_bundle,
)

ROOT = Path(__file__).resolve().parents[2]


def test_telegram_rehearsal_stub_executes_deployed_gateway_contract() -> None:
    contract = ROOT / "tests/rehearsal/browser-stub/telegram-server.test.mjs"
    result = subprocess.run(  # noqa: S603 - fixed repository-owned contract
        ["node", "--test", os.fspath(contract)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_rehearsal_adoption_fixture_uses_one_monitored_usd_cabinet() -> None:
    parsed = parse_adoption_bundle_json(json.dumps(build_adoption_bundle()))

    assert parsed.entity_counts["accounts"] == 1
    assert parsed.entity_counts["recipients"] == 1
    assert parsed.entity_counts["system_settings"] == 1
    assert parsed.sections.offer_rules[0].currency == "USD"
    assert parsed.sections.recipients[0].role == "owner"
    assert parsed.sections.observer_settings is not None
    assert parsed.sections.observer_settings.campaign_ids == ["9001"]


def test_rehearsal_refuses_non_ephemeral_or_unacknowledged_host(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tests.rehearsal.single_slot.os.geteuid", lambda: 0)
    docker_config = tmp_path / "docker"
    docker_config.mkdir()
    (docker_config / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("FB_AGENT_REHEARSAL_ACK", raising=False)
    with pytest.raises(RehearsalError, match="ephemeral Actions host"):
        _require_ci_acknowledgement()

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RehearsalError, match="ACK"):
        _require_ci_acknowledgement()

    monkeypatch.setenv("FB_AGENT_REHEARSAL_ACK", "single-slot")
    _require_ci_acknowledgement()


def test_rehearsal_requires_root_and_absolute_existing_docker_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FB_AGENT_REHEARSAL_ACK", "single-slot")
    monkeypatch.setattr("tests.rehearsal.single_slot.os.geteuid", lambda: 501)
    with pytest.raises(RehearsalError, match="root privileges"):
        _require_ci_acknowledgement()

    monkeypatch.setattr("tests.rehearsal.single_slot.os.geteuid", lambda: 0)
    monkeypatch.setenv("DOCKER_CONFIG", "relative/docker")
    with pytest.raises(RehearsalError, match="absolute path"):
        _require_ci_acknowledgement()

    docker_config = tmp_path / "missing-docker-config"
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
    with pytest.raises(RehearsalError, match="existing config.json"):
        _require_ci_acknowledgement()


def test_rehearsal_profile_seed_is_nested_private_and_ownership_is_recursive(tmp_path) -> None:
    profile = tmp_path / "desktop-profile-seed"
    _write_profile_seed(profile)

    assert stat.S_IMODE(profile.stat().st_mode) == 0o700
    assert stat.S_IMODE((profile / ".fb-agent-vision-profile-v1").stat().st_mode) == 0o600
    assert stat.S_IMODE((profile / "browser").stat().st_mode) == 0o700
    assert stat.S_IMODE((profile / "browser" / "Default").stat().st_mode) == 0o700
    assert stat.S_IMODE((profile / "browser" / "Default" / "Preferences").stat().st_mode) == 0o600
    _assert_tree_ownership(profile, uid=os.getuid(), gid=os.getgid())

    with pytest.raises(RehearsalError, match="ownership mismatch"):
        _assert_tree_ownership(profile, uid=os.getuid() + 1, gid=os.getgid())
