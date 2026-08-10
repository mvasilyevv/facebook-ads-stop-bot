from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATE_SCRIPT = ROOT / "scripts" / "release-state.py"
CLUSTER_LINE = f"FB_AGENT_BOOTSTRAP_CLUSTER_ID={'a' * 32}\n"
pytestmark = pytest.mark.timeout(15)


def _state(*args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(STATE_SCRIPT), *(str(value) for value in args)],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


def _file(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _prepare(
    state_root: Path,
    release_root: Path,
    release_id: str,
    color: str,
) -> Path:
    release_dir = release_root / release_id
    release_dir.mkdir(mode=0o700)
    _file(release_dir / "payload.txt", f"{release_id}\n")
    service = release_dir / "deploy" / "systemd" / "fb-agent.service"
    service.parent.mkdir(parents=True)
    _file(service, "[Unit]\nDescription=Test FB Agent\n")
    scripts = release_dir / "scripts"
    scripts.mkdir()
    for name in (
        "platform-alloy-agent.sh",
        "install-platform-units.sh",
        "install-server-units.sh",
        "install-alloy-agent-unit.sh",
        "platform-desktop-release.sh",
    ):
        body = "exit 0"
        if name == "platform-desktop-release.sh":
            body = (
                'printf \'desktop %s\\n\' "$*" >>"${FB_AGENT_TEST_CALLS:?missing test calls path}"'
            )
        _mock(scripts / name, body)
    _state(
        "manifest-write",
        "--release-dir",
        release_dir,
        "--manifest",
        release_dir / ".fb-agent-source-manifest.json",
    )
    for directory, directory_names, file_names in os.walk(
        release_dir,
        topdown=False,
    ):
        root = Path(directory)
        for name in file_names:
            path = root / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
        for name in directory_names:
            path = root / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
    release_dir.chmod(release_dir.stat().st_mode & ~0o222)
    app_env = _file(
        state_root / f"{release_id}.app.env",
        f"VALUE={release_id}\n{CLUSTER_LINE}",
    )
    args: list[object] = [
        "prepare",
        "--state-root",
        state_root,
        "--release-root",
        release_root,
        "--release-dir",
        release_dir,
        "--app-env",
        app_env,
        "--release-id",
        release_id,
        "--color",
        color,
    ]
    manifest = _file(state_root / f"{release_id}.release.env", f"RELEASE_ID={release_id}\n")
    args.extend(("--release-env", manifest))
    result = _state(*args)
    return Path(result.stdout.strip())


def _accept(state_root: Path, candidate: Path, failpoint: str | None = None) -> int:
    _state("begin", "--state-root", state_root, "--candidate-state", candidate)
    _state(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        int(time.time()) + 120,
    )
    _state("stage", "--state-root", state_root, "--stage", "accepted")
    args: list[object] = ["commit", "--state-root", state_root]
    if failpoint:
        args.extend(("--failpoint", failpoint))
    return _state(*args, check=failpoint is None).returncode


def _complete(state_root: Path) -> None:
    _state(
        "stage",
        "--state-root",
        state_root,
        "--stage",
        "desktop_adopted",
    )
    _state("complete", "--state-root", state_root)


def _mock(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _runtime(tmp_path: Path, calls: Path) -> tuple[Path, dict[str, str]]:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    shutil.copy2(STATE_SCRIPT, runtime / "release-state.py")
    shutil.copy2(ROOT / "scripts" / "reconcile-platform-release.sh", runtime)
    shutil.copy2(ROOT / "scripts" / "browser-control-env.sh", runtime)
    (runtime / "reconcile-platform-release.sh").chmod(0o755)
    _mock(
        runtime / "bluegreen-switch-caddy.sh",
        f"printf 'caddy %s\\n' \"$*\" >>{calls!s}",
    )
    _mock(
        runtime / "bluegreen-worker-handoff.sh",
        f"printf 'handoff %s\\n' \"$*\" >>{calls!s}",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ("curl", "flock", "install", "logger", "systemctl"):
        _mock(bin_dir / command, f"printf '{command} %s\\n' \"$*\" >>{calls!s}")
    _mock(
        bin_dir / "docker",
        f'printf \'docker app_env=%s %s\\n\' "${{APP_ENV_FILE:-}}" "$*" >>{calls!s}',
    )
    _mock(
        bin_dir / "timeout",
        f'printf \'timeout %s\\n\' "$*" >>{calls!s}\nshift 2\nexec "$@"',
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        "FB_AGENT_TEST_CALLS": str(calls),
    }
    return runtime, env


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    app_root = tmp_path / "fb-agent"
    state_root = app_root / "shared"
    release_root = app_root / "releases"
    state_root.mkdir(parents=True, mode=0o700)
    release_root.mkdir(mode=0o700)
    _file(state_root / "pgbackrest.env", "BACKUP=ready\n")
    _file(
        state_root / "browser-control.env",
        (
            "BROWSER_MAINTENANCE_CAPABILITY_SECRET="
            + "s" * 64
            + "\nBROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE="
            + "a" * 64
            + "\nBROWSER_OPERATION_CAPABILITY_SECRET_META_API="
            + "m" * 64
            + "\nBROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR="
            + "c" * 64
            + "\nBROWSER_AUTHORITY_CONSUMER_TOKEN="
            + "t" * 64
            + "\n"
        ),
    )
    _file(
        state_root / "browser-maintenance.env",
        "BROWSER_MAINTENANCE_CAPABILITY_SECRET=" + "s" * 64 + "\n",
    )
    _file(
        state_root / "browser-autopause.env",
        "BROWSER_OPERATION_CAPABILITY_SECRET=" + "a" * 64 + "\n",
    )
    _file(
        state_root / "browser-meta-api.env",
        "BROWSER_OPERATION_CAPABILITY_SECRET=" + "m" * 64 + "\n",
    )
    _file(
        state_root / "browser-campaign-creator.env",
        "BROWSER_OPERATION_CAPABILITY_SECRET=" + "c" * 64 + "\n",
    )
    _file(
        state_root / "browser-authority.env",
        "BROWSER_AUTHORITY_CONSUMER_TOKEN=" + "t" * 64 + "\n",
    )
    _file(state_root / ".env", "OPERATOR=desired\n")
    return app_root, state_root, release_root


def _run_reconciler(
    runtime: Path,
    env: dict[str, str],
    app_root: Path,
    *arguments: object,
):
    return subprocess.run(
        [
            str(runtime / "reconcile-platform-release.sh"),
            "--deadline-seconds",
            "180",
            *(str(value) for value in arguments),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env | {"FB_AGENT_ROOT": str(app_root)},
    )


def test_boot_reconciler_is_a_clean_noop_before_first_release_journal(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    calls.touch()
    runtime, env = _runtime(tmp_path, calls)
    app_root, _state_root, _release_root = _layout(tmp_path)

    result = subprocess.run(
        [
            str(runtime / "reconcile-platform-release.sh"),
            "--boot",
            "--deadline-seconds",
            "180",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env | {"FB_AGENT_ROOT": str(app_root)},
    )

    assert result.returncode == 0, result.stderr
    assert "boot reconciliation is a clean no-op" in result.stderr
    log = calls.read_text(encoding="utf-8")
    assert "caddy " not in log
    assert "docker " not in log
    assert "handoff " not in log


def test_unselected_first_candidate_is_retired_before_different_release_retry(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    calls.touch()
    runtime, env = _runtime(tmp_path, calls)
    app_root, state_root, release_root = _layout(tmp_path)
    failed_candidate = _prepare(state_root, release_root, "release-1", "blue")
    _state("begin", "--state-root", state_root, "--candidate-state", failed_candidate)
    _state("stage", "--state-root", state_root, "--stage", "candidate_started")

    result = _run_reconciler(runtime, env, app_root)

    assert result.returncode == 0, result.stderr
    assert "clean bootstrap may resume" in result.stderr
    assert not (state_root / "release-transaction.json").exists()
    log = calls.read_text(encoding="utf-8")
    assert "docker app_env=" in log
    assert "compose -p fb_agent_blue" in log
    assert "stop --timeout 60" in log

    retry = _prepare(state_root, release_root, "release-2", "blue")
    _state("begin", "--state-root", state_root, "--candidate-state", retry)
    assert (state_root / "release-transaction.json").is_file()


def test_reconcile_crash_before_pointer_restores_previous_and_aborts(tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    calls.touch()
    runtime, env = _runtime(tmp_path, calls)
    app_root, state_root, release_root = _layout(tmp_path)
    previous = _prepare(state_root, release_root, "release-1", "blue")
    _accept(state_root, previous)
    _state("ensure-links", "--state-root", state_root, "--root-dir", app_root)
    _complete(state_root)
    candidate = _prepare(state_root, release_root, "release-2", "green")

    assert _accept(state_root, candidate, "before-pointer") == 86
    result = _run_reconciler(runtime, env, app_root)

    assert result.returncode == 0, result.stderr
    active = _state(
        "get", "--state-root", state_root, "--source", "active", "--field", "state_dir"
    ).stdout.strip()
    assert Path(active) == previous
    assert not (state_root / "release-transaction.json").exists()
    assert (state_root / ".env").read_text() == "OPERATOR=desired\n"
    log = calls.read_text()
    assert "caddy --color blue" in log
    assert "handoff --from-color green" in log
    assert "--to-color blue" in log


def test_reconcile_initial_blue_candidate_converges_forward_only(tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    calls.touch()
    runtime, env = _runtime(tmp_path, calls)
    app_root, state_root, release_root = _layout(tmp_path)
    candidate = _prepare(state_root, release_root, "release-1", "blue")
    _state("begin", "--state-root", state_root, "--candidate-state", candidate)
    _state("stage", "--state-root", state_root, "--stage", "candidate_started")
    deadline_epoch = int(time.time()) + 120
    _state(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        deadline_epoch,
    )
    crashed = _state(
        "select-initial",
        "--state-root",
        state_root,
        "--failpoint",
        "after-policy",
        check=False,
    )
    assert crashed.returncode == 88

    result = _run_reconciler(runtime, env, app_root)

    assert result.returncode == 0, result.stderr
    active = _state(
        "get", "--state-root", state_root, "--source", "active", "--field", "state_dir"
    ).stdout.strip()
    assert Path(active) == candidate
    assert not (state_root / "release-transaction.json").exists()
    assert (state_root / ".env").read_text() == "OPERATOR=desired\n"
    log = calls.read_text()
    assert "caddy --color blue" in log
    assert "telegram_webhook_configurator" in log
    assert "handoff --to-color blue" in log
    assert "--from-color" not in log


def test_reconcile_crash_after_pointer_converges_forward_once(tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    calls.touch()
    runtime, env = _runtime(tmp_path, calls)
    app_root, state_root, release_root = _layout(tmp_path)
    previous = _prepare(state_root, release_root, "release-1", "blue")
    _accept(state_root, previous)
    _state("ensure-links", "--state-root", state_root, "--root-dir", app_root)
    _complete(state_root)
    candidate = _prepare(state_root, release_root, "release-2", "green")

    assert _accept(state_root, candidate, "after-pointer") == 87
    deadline_epoch = int(
        _state(
            "get",
            "--state-root",
            state_root,
            "--source",
            "journal",
            "--field",
            "cutover_deadline_epoch",
        ).stdout
    )
    result = _run_reconciler(runtime, env, app_root)

    assert result.returncode == 0, result.stderr
    active = _state(
        "get", "--state-root", state_root, "--source", "active", "--field", "state_dir"
    ).stdout.strip()
    assert Path(active) == candidate
    assert not (state_root / "release-transaction.json").exists()
    assert (state_root / ".env").read_text() == "OPERATOR=desired\n"
    log = calls.read_text()
    assert "caddy --color green" in log
    assert "handoff --from-color blue" in log
    assert "--to-color green" in log
    assert "desktop --release-env" in log
    assert log.count(f"--deadline-epoch {deadline_epoch}") >= 2


def test_normal_retry_does_not_replace_expired_immutable_cutover_deadline(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    calls.touch()
    runtime, env = _runtime(tmp_path, calls)
    app_root, state_root, release_root = _layout(tmp_path)
    previous = _prepare(state_root, release_root, "release-1", "blue")
    _accept(state_root, previous)
    _state("ensure-links", "--state-root", state_root, "--root-dir", app_root)
    _complete(state_root)
    candidate = _prepare(state_root, release_root, "release-2", "green")
    _state("begin", "--state-root", state_root, "--candidate-state", candidate)
    _state(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        int(time.time()) + 120,
    )
    _state("stage", "--state-root", state_root, "--stage", "accepted")
    crashed = _state(
        "commit",
        "--state-root",
        state_root,
        "--failpoint",
        "after-pointer",
        check=False,
    )
    assert crashed.returncode == 87
    journal_path = state_root / "release-transaction.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["cutover_deadline_epoch"] = int(time.time()) - 60
    journal_path.write_text(
        json.dumps(journal),
        encoding="utf-8",
    )
    journal_path.chmod(0o600)

    result = _run_reconciler(runtime, env, app_root)

    assert result.returncode != 0
    assert "absolute reconciliation deadline has expired" in result.stderr
    assert "bounded boot recovery" not in result.stderr
    active = _state(
        "get",
        "--state-root",
        state_root,
        "--source",
        "active",
        "--field",
        "state_dir",
    ).stdout.strip()
    assert Path(active) == candidate
    assert journal_path.exists()
    assert not (state_root / "rollback-failed.json").exists()


def test_boot_recovery_uses_one_new_bounded_window_and_preserves_breach_evidence(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    calls.touch()
    runtime, env = _runtime(tmp_path, calls)
    app_root, state_root, release_root = _layout(tmp_path)
    previous = _prepare(state_root, release_root, "release-1", "blue")
    _accept(state_root, previous)
    _state("ensure-links", "--state-root", state_root, "--root-dir", app_root)
    _complete(state_root)
    candidate = _prepare(state_root, release_root, "release-2", "green")
    _state("begin", "--state-root", state_root, "--candidate-state", candidate)
    _state(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        int(time.time()) + 120,
    )
    _state("stage", "--state-root", state_root, "--stage", "accepted")
    crashed = _state(
        "commit",
        "--state-root",
        state_root,
        "--failpoint",
        "after-pointer",
        check=False,
    )
    assert crashed.returncode == 87
    journal_path = state_root / "release-transaction.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    expired_deadline = int(time.time()) - 60
    journal["cutover_deadline_epoch"] = expired_deadline
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    journal_path.chmod(0o600)

    started_at = int(time.time())
    result = _run_reconciler(runtime, env, app_root, "--boot")

    assert result.returncode == 0, result.stderr
    assert "persisted cutover deadline breached" in result.stderr
    marker = json.loads((state_root / "rollback-failed.json").read_text(encoding="utf-8"))
    assert marker["failures"] == [f"cutover_deadline_breached:original_{expired_deadline}"]
    assert not journal_path.exists()
    desktop_line = next(
        line
        for line in calls.read_text(encoding="utf-8").splitlines()
        if line.startswith("desktop ")
    )
    recovery_deadline = int(desktop_line.rsplit("--deadline-epoch ", 1)[1])
    assert started_at < recovery_deadline <= int(time.time()) + 180


def test_explicit_expired_deadline_is_never_replaced_even_during_boot(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    calls.touch()
    runtime, env = _runtime(tmp_path, calls)
    app_root, state_root, release_root = _layout(tmp_path)
    previous = _prepare(state_root, release_root, "release-1", "blue")
    _accept(state_root, previous)
    _complete(state_root)
    candidate = _prepare(state_root, release_root, "release-2", "green")
    _state("begin", "--state-root", state_root, "--candidate-state", candidate)
    journal_path = state_root / "release-transaction.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    expired_deadline = int(time.time()) - 60
    journal["cutover_deadline_epoch"] = expired_deadline
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    journal_path.chmod(0o600)

    result = _run_reconciler(
        runtime,
        env,
        app_root,
        "--boot",
        "--deadline-epoch",
        expired_deadline,
    )

    assert result.returncode != 0
    assert "absolute reconciliation deadline has expired" in result.stderr
    assert "bounded boot recovery" not in result.stderr
    assert journal_path.exists()
    assert not (state_root / "rollback-failed.json").exists()


def test_webhook_success_then_precommit_failure_restores_committed_webhook(
    tmp_path: Path,
) -> None:
    """The external Telegram pointer follows the atomic local release pointer."""

    calls = tmp_path / "calls.log"
    calls.touch()
    runtime, env = _runtime(tmp_path, calls)
    app_root, state_root, release_root = _layout(tmp_path)
    previous = _prepare(state_root, release_root, "release-1", "blue")
    _accept(state_root, previous)
    _state("ensure-links", "--state-root", state_root, "--root-dir", app_root)
    _complete(state_root)
    committed_app_env = _state(
        "get", "--state-root", state_root, "--source", "active", "--field", "app_env"
    ).stdout.strip()
    committed_release_env = _state(
        "get",
        "--state-root",
        state_root,
        "--source",
        "active",
        "--field",
        "release_env",
    ).stdout.strip()
    candidate = _prepare(state_root, release_root, "release-2", "green")

    _state("begin", "--state-root", state_root, "--candidate-state", candidate)
    deadline_epoch = int(time.time()) + 120
    _state(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        deadline_epoch,
    )
    # This is the production failpoint immediately after setWebhook: accepted
    # locally, but the atomic release pointer has not moved yet.
    _state("stage", "--state-root", state_root, "--stage", "accepted")
    crashed = _state(
        "commit",
        "--state-root",
        state_root,
        "--failpoint",
        "before-pointer",
        check=False,
    )
    assert crashed.returncode == 86

    result = _run_reconciler(runtime, env, app_root)

    assert result.returncode == 0, result.stderr
    log = calls.read_text()
    assert "caddy --color blue" in log
    assert (
        f"docker app_env={committed_app_env} "
        f"compose -p fb_agent_blue --env-file {committed_release_env}"
    ) in log
    assert "--profile release run --rm telegram_webhook_configurator" in log
    assert "handoff --from-color green" in log
    assert f"--deadline-epoch {deadline_epoch}" in log
    webhook_position = log.index("telegram_webhook_configurator")
    worker_position = log.index("handoff --from-color green")
    assert webhook_position < worker_position
    assert not (state_root / "release-transaction.json").exists()


def test_reconciler_never_resets_persisted_absolute_cutover_budget(tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    calls.touch()
    runtime, env = _runtime(tmp_path, calls)
    app_root, state_root, release_root = _layout(tmp_path)
    previous = _prepare(state_root, release_root, "release-1", "blue")
    _accept(state_root, previous)
    _complete(state_root)
    candidate = _prepare(state_root, release_root, "release-2", "green")
    _state("begin", "--state-root", state_root, "--candidate-state", candidate)
    deadline_epoch = int(time.time()) + 120
    _state(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        deadline_epoch,
    )

    mismatched = _run_reconciler(
        runtime,
        env,
        app_root,
        "--deadline-epoch",
        deadline_epoch + 1,
    )
    assert mismatched.returncode != 0
    assert (
        "explicit deadline does not match immutable journal cutover deadline" in mismatched.stderr
    )

    result = _run_reconciler(runtime, env, app_root)

    assert result.returncode == 0, result.stderr
    log = calls.read_text()
    assert f"--deadline-epoch {deadline_epoch}" in log
    timeout_durations = [
        int(line.split()[2].removesuffix("s"))
        for line in log.splitlines()
        if line.startswith("timeout --signal=KILL ")
    ]
    assert timeout_durations
    assert max(timeout_durations) <= 120
