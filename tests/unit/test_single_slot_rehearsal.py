from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from core.adoption.bundle import parse_adoption_bundle_json
from tests.rehearsal import single_slot
from tests.rehearsal.single_slot import (
    RehearsalError,
    _assert_tree_ownership,
    _partition_failpoints,
    _rehearsal_failpoints,
    _require_ci_acknowledgement,
    _validate_rehearsal_request,
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


def test_failpoint_round_robin_partition_is_ordered_complete_and_unique() -> None:
    failpoints = [f"step-{index}" for index in range(16)]

    shards = [
        _partition_failpoints(failpoints, shard_index=index, shard_count=4) for index in range(4)
    ]

    assert shards == [failpoints[index::4] for index in range(4)]
    flattened = [step for shard in shards for step in shard]
    assert Counter(flattened) == Counter(failpoints)
    assert len(flattened) == len(set(flattened))


def test_failpoint_round_robin_partition_handles_an_uneven_tail() -> None:
    failpoints = [f"step-{index}" for index in range(10)]

    shards = [
        _partition_failpoints(failpoints, shard_index=index, shard_count=4) for index in range(4)
    ]

    assert [len(shard) for shard in shards] == [3, 3, 2, 2]
    assert Counter(step for shard in shards for step in shard) == Counter(failpoints)


@pytest.mark.parametrize(
    ("scenario", "shard_index", "shard_count", "message"),
    [
        ("failpoints", 0, 0, "at least 1"),
        ("failpoints", -1, 4, "between 0"),
        ("failpoints", 4, 4, "between 0"),
        ("acceptance", 1, 4, "only for the failpoints"),
        ("full", 0, 4, "only for the failpoints"),
        ("unknown", 0, 1, "unsupported"),
    ],
)
def test_rehearsal_rejects_invalid_scenario_shard_combinations(
    scenario: str,
    shard_index: int,
    shard_count: int,
    message: str,
) -> None:
    with pytest.raises(RehearsalError, match=message):
        _validate_rehearsal_request(
            scenario,
            shard_index=shard_index,
            shard_count=shard_count,
        )


def test_ordered_failpoints_are_loaded_only_from_the_shipped_bundle(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "fbctl.pyz"
    ordered = ["pull", "stop_runtime", "before_promote"]
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(arguments: list[str], **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout=json.dumps(
                {
                    "schema": "fb-agent-rehearsal-failpoints/v1",
                    "failpoints": ordered,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(single_slot, "_run", fake_run)

    assert _rehearsal_failpoints(bundle) == ordered
    assert calls == [
        (
            [sys.executable, "-B", os.fspath(bundle), "deploy", "--list-failpoints"],
            {"capture": True},
        )
    ]


def test_sharded_failpoints_recover_each_selected_step_and_keep_global_fencing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failpoints = [
        "preflight",
        "pull",
        "stop_runtime",
        "start_infra",
        "migrate",
        "before_promote",
    ]
    events: list[tuple[str, object]] = []

    monkeypatch.setattr(single_slot, "_rehearsal_failpoints", lambda _bundle: failpoints)
    monkeypatch.setattr(
        single_slot,
        "_assert_workers_off",
        lambda cluster_id: events.append(("workers_off", cluster_id)),
    )

    def fake_run(arguments: list[str], **_kwargs):
        if "--fail-after-step" in arguments:
            step = arguments[arguments.index("--fail-after-step") + 1]
            events.append(("inject", step))
            return subprocess.CompletedProcess(
                arguments,
                1,
                stdout="",
                stderr=json.dumps({"step": step}),
            )
        events.append(("recover", "--enable-scanning" in arguments))
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    monkeypatch.setattr(single_slot, "_run", fake_run)

    single_slot._exercise_failpoints(
        tmp_path / "fbctl.pyz",
        tmp_path / "root",
        "cluster",
        shard_index=1,
        shard_count=2,
    )

    assert events == [
        ("inject", "pull"),
        ("recover", True),
        ("inject", "start_infra"),
        ("workers_off", "cluster"),
        ("recover", True),
        ("inject", "before_promote"),
        ("workers_off", "cluster"),
        ("recover", True),
    ]


def test_scenarios_preserve_full_and_split_failpoint_acceptance_sequences(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    bundle = tmp_path / "fbctl.pyz"
    root = tmp_path / "root"

    monkeypatch.setattr(
        single_slot,
        "_exercise_failpoints",
        lambda _bundle, _root, _cluster, *, shard_index, shard_count: events.append(
            ("failpoints", shard_index, shard_count)
        ),
    )
    monkeypatch.setattr(single_slot, "_final_smoke", lambda: events.append(("smoke",)))
    monkeypatch.setattr(
        single_slot,
        "_exercise_notification_lifecycle",
        lambda telegram, fingerprint: events.append(("telegram", telegram, fingerprint)),
    )

    def fake_run(arguments: list[str], **_kwargs):
        events.append(("deploy", "--enable-scanning" in arguments))
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    monkeypatch.setattr(single_slot, "_run", fake_run)

    common = {
        "bundle": bundle,
        "root": root,
        "cluster_id": "cluster",
        "telegram": "telegram",
        "fingerprint": "fingerprint",
    }
    single_slot._exercise_scenario(
        scenario="full",
        shard_index=0,
        shard_count=1,
        **common,
    )
    assert events == [
        ("failpoints", 0, 1),
        ("smoke",),
        ("telegram", "telegram", "fingerprint"),
        ("deploy", False),
        ("smoke",),
    ]

    events.clear()
    single_slot._exercise_scenario(
        scenario="failpoints",
        shard_index=2,
        shard_count=4,
        **common,
    )
    assert events == [("failpoints", 2, 4), ("smoke",)]

    events.clear()
    single_slot._exercise_scenario(
        scenario="acceptance",
        shard_index=0,
        shard_count=1,
        **common,
    )
    assert events == [
        ("deploy", True),
        ("smoke",),
        ("telegram", "telegram", "fingerprint"),
        ("deploy", False),
        ("smoke",),
    ]


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ([], {"scenario": "full", "shard_index": 0, "shard_count": 1}),
        (
            ["--scenario", "failpoints", "--shard-index", "2", "--shard-count", "4"],
            {"scenario": "failpoints", "shard_index": 2, "shard_count": 4},
        ),
    ],
)
def test_rehearsal_cli_defaults_to_full_and_forwards_explicit_shards(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
    expected: dict[str, object],
) -> None:
    bundle = tmp_path / "fbctl.pyz"
    release = tmp_path / "release.json"
    bundle.write_text("bundle", encoding="utf-8")
    release.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_rehearse(
        actual_bundle: Path,
        actual_release: Path,
        source_root: Path,
        **kwargs,
    ) -> None:
        captured.update(
            {
                "bundle": actual_bundle,
                "release": actual_release,
                "source_root": source_root,
                **kwargs,
            }
        )

    monkeypatch.setattr(single_slot, "rehearse", fake_rehearse)

    assert (
        single_slot.main(
            [
                os.fspath(bundle),
                os.fspath(release),
                "--source-root",
                os.fspath(tmp_path),
                *extra,
            ]
        )
        == 0
    )
    assert captured == {
        "bundle": bundle.resolve(),
        "release": release.resolve(),
        "source_root": tmp_path.resolve(),
        **expected,
    }


def test_failure_reason_names_the_invariant_without_leaking_command_secrets() -> None:
    """Упавший CI обязан сказать, какой инвариант нарушен.

    Раньше печатался только класс исключения, и причина терялась. При этом
    аргументы внешней команды могут нести секреты, поэтому от неё остаются
    только имя программы и код возврата.
    """
    reason = single_slot._failure_reason(
        single_slot.RehearsalError("release.json contains a mutable image")
    )
    assert reason == "RehearsalError: release.json contains a mutable image"

    secret = "POSTGRES_PASSWORD=never-print-this"
    reason = single_slot._failure_reason(
        subprocess.CalledProcessError(2, ["docker", "run", "--env", secret])
    )
    assert reason == "CalledProcessError: docker exited with 2"
    assert secret not in reason

    reason = single_slot._failure_reason(
        FileNotFoundError(2, "No such file or directory", "/tmp/missing-bundle")
    )
    assert reason == "FileNotFoundError: No such file or directory"


def test_deploy_outcome_distinguishes_no_stop_from_stopping_elsewhere() -> None:
    """«Не остановился» и «остановился не там» — разные диагнозы.

    Вывод failpoint-деплоя захватывается и в лог CI не попадает, поэтому без
    этого различия причина провала не восстанавливается по логу вообще.
    """
    promoted = subprocess.CompletedProcess(["fbctl"], 0, stdout="", stderr="")
    assert single_slot._deploy_outcome(promoted) == "deploy exited 0 and promoted the release"

    elsewhere = subprocess.CompletedProcess(
        ["fbctl"],
        1,
        stdout="",
        stderr='[fbctl] step=migrate completed\n{"error": "rehearsal failpoint triggered", "step": "failure_cleanup"}\n',
    )
    assert single_slot._deploy_outcome(elsewhere) == (
        "deploy exited 1 at step failure_cleanup: rehearsal failpoint triggered"
    )

    unstructured = subprocess.CompletedProcess(["fbctl"], 2, stdout="", stderr="boom\n")
    assert single_slot._deploy_outcome(unstructured) == (
        "deploy exited 2 without a structured fbctl error"
    )
