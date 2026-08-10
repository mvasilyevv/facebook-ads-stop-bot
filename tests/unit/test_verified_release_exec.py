from __future__ import annotations

import importlib.util
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
VERIFIER_SCRIPT = ROOT / "scripts" / "verified-release-exec.py"
IMAGE = "registry.example/fb-agent@sha256:" + "a" * 64


def _state(*arguments: object) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(STATE_SCRIPT), *(str(value) for value in arguments)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


def _write(path: Path, content: str, *, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _seal_tree(root: Path) -> None:
    for directory, directory_names, file_names in os.walk(root, topdown=False):
        current = Path(directory)
        for name in file_names:
            path = current / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
        for name in directory_names:
            path = current / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
    root.chmod(root.stat().st_mode & ~0o222)


def _stable_runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "stable" / "releases" / "digest"
    runtime.mkdir(parents=True)
    shutil.copy2(STATE_SCRIPT, runtime / "release-state.py")
    shutil.copy2(VERIFIER_SCRIPT, runtime / "verified-release-exec.py")
    (runtime / "release-state.py").chmod(0o444)
    (runtime / "verified-release-exec.py").chmod(0o555)
    runtime.chmod(0o555)
    return runtime / "verified-release-exec.py"


def _release(root: Path, marker: Path) -> Path:
    release = root / "releases" / "release-1"
    scripts = release / "scripts"
    scripts.mkdir(parents=True)
    entrypoint = scripts / "probe.sh"
    entrypoint.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'printf "%s|%s|%s|%s\\n" "$1" "$FB_AGENT_RELEASE_DIR" '
        '"$FB_AGENT_ACTIVE_STATE_DIR" "$FB_AGENT_APP_STATE_DIR" '
        f">{marker!s}\n",
        encoding="utf-8",
    )
    entrypoint.chmod(0o755)
    pinned_probe = scripts / "pinned-state-probe.sh"
    pinned_probe.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'python3 - "$FB_AGENT_ROOT/shared" "$2" <<\'PY\'\n'
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "shared = Path(sys.argv[1])\n"
        "target = sys.argv[2]\n"
        'temporary = shared / f".active-state-after-verify-{os.getpid()}"\n'
        'temporary.symlink_to(Path("active-states") / target)\n'
        'os.replace(temporary, shared / "active-state")\n'
        "PY\n"
        "pinned=\"$(sed -n 's/^VALUE=//p' "
        '"$FB_AGENT_APP_STATE_DIR/app.env")"\n'
        "live=\"$(sed -n 's/^VALUE=//p' "
        '"$FB_AGENT_ROOT/shared/active-state/app.env")"\n'
        'printf "%s|%s|%s|%s\\n" "$pinned" "$live" '
        '"$FB_AGENT_ACTIVE_STATE_DIR" "$FB_AGENT_APP_STATE_DIR" >"$1"\n',
        encoding="utf-8",
    )
    pinned_probe.chmod(0o755)
    _state(
        "manifest-write",
        "--release-dir",
        release,
        "--manifest",
        release / ".fb-agent-source-manifest.json",
    )
    _seal_tree(release)
    return release


def _root(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "fb-agent"
    shared = root / "shared"
    releases = root / "releases"
    shared.mkdir(parents=True, mode=0o700)
    releases.mkdir(mode=0o755)
    root.chmod(0o755)
    shared.chmod(0o700)
    return root, shared, releases


def _release_env() -> str:
    return f"RELEASE_ID=release-1\nBROWSER_AGENT_IMAGE={IMAGE}\nDESKTOP_WEBTOP_IMAGE={IMAGE}\n"


def _commit_app_state(
    root: Path,
    shared: Path,
    release: Path,
    *,
    prefix: str,
    value: str = "safe",
    color: str = "blue",
) -> Path:
    app_env = _write(shared / f"{prefix}.app.env", f"VALUE={value}\n")
    release_env = _write(
        shared / f"{prefix}.release.env",
        _release_env(),
    )
    state_dir = Path(
        _state(
            "prepare",
            "--state-root",
            shared,
            "--release-root",
            root / "releases",
            "--release-dir",
            release,
            "--app-env",
            app_env,
            "--release-env",
            release_env,
            "--release-id",
            "release-1",
            "--color",
            color,
        ).stdout.strip()
    )
    _state("begin", "--state-root", shared, "--candidate-state", state_dir)
    _state(
        "arm-cutover",
        "--state-root",
        shared,
        "--deadline-epoch",
        int(time.time()) + 120,
    )
    _state("stage", "--state-root", shared, "--stage", "accepted")
    _state("commit", "--state-root", shared)
    _state("stage", "--state-root", shared, "--stage", "desktop_adopted")
    _state("complete", "--state-root", shared)
    return state_dir


def _app_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    verifier = _stable_runtime(tmp_path)
    root, shared, _ = _root(tmp_path)
    marker = tmp_path / "executed"
    release = _release(root, marker)
    state_dir = _commit_app_state(
        root,
        shared,
        release,
        prefix="candidate",
    )
    return verifier, root, marker, state_dir


def _desktop_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    verifier = _stable_runtime(tmp_path)
    root, shared, _ = _root(tmp_path)
    marker = tmp_path / "executed"
    release = _release(root, marker)
    _commit_app_state(
        root,
        shared,
        release,
        prefix="desktop-app-candidate",
    )
    states = shared / "desktop-states"
    states.mkdir(mode=0o700)
    app_env = _write(shared / "desktop-candidate.app.env", "VALUE=safe\n")
    release_env = _write(
        shared / "desktop-candidate.release.env",
        _release_env(),
    )
    fingerprint = _state(
        "desktop-digest",
        "--release-dir",
        release,
        "--app-env",
        app_env,
        "--release-env",
        release_env,
    ).stdout.strip()
    state_dir = states / f"release-1-{fingerprint[:16]}"
    state_dir.mkdir(mode=0o700)
    shutil.copy2(app_env, state_dir / "app.env")
    shutil.copy2(release_env, state_dir / "release-images.env")
    _write(state_dir / "fingerprint", f"{fingerprint}\n")
    (state_dir / "release").symlink_to(release)
    (shared / "active-desktop-state").symlink_to(Path("desktop-states") / state_dir.name)
    return verifier, root, marker, state_dir


def _run(
    verifier: Path,
    root: Path,
    state: str,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(verifier),
            "--root",
            str(root),
            "--state",
            state,
            "--entrypoint",
            "scripts/probe.sh",
            "--",
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _release_state_module():
    spec = importlib.util.spec_from_file_location(
        "release_state_under_test",
        STATE_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_active_state_flip_wrapper(
    verifier: Path,
    *,
    target_state: Path,
) -> None:
    runtime = verifier.parent
    state_verifier = runtime / "release-state.py"
    real_state_verifier = runtime / "release-state-real.py"
    runtime.chmod(0o755)
    state_verifier.rename(real_state_verifier)
    state_verifier.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        'if len(sys.argv) > 1 and sys.argv[1] == "runtime-verify":\n'
        '    index = sys.argv.index("--state-root") + 1\n'
        "    state_root = Path(sys.argv[index])\n"
        '    temporary = state_root / f".active-state-race-{os.getpid()}"\n'
        f'    os.symlink("active-states/{target_state.name}", temporary)\n'
        '    os.replace(temporary, state_root / "active-state")\n'
        f"os.execv(sys.executable, [sys.executable, {str(real_state_verifier)!r}, "
        "*sys.argv[1:]])\n",
        encoding="utf-8",
    )
    real_state_verifier.chmod(0o444)
    state_verifier.chmod(0o444)
    runtime.chmod(0o555)


def test_desktop_digest_is_path_independent_and_binds_full_state_content(
    tmp_path: Path,
) -> None:
    root, shared, _ = _root(tmp_path)
    release = _release(root, tmp_path / "unused-marker")
    first_app = _write(shared / "first.app.env", "VALUE=safe\n")
    second_app = _write(shared / "second.app.env", "VALUE=safe\n")
    first_release = _write(shared / "first.release.env", _release_env())
    second_release = _write(shared / "second.release.env", _release_env())

    first = _state(
        "desktop-digest",
        "--release-dir",
        release,
        "--app-env",
        first_app,
        "--release-env",
        first_release,
    ).stdout.strip()
    second = _state(
        "desktop-digest",
        "--release-dir",
        release,
        "--app-env",
        second_app,
        "--release-env",
        second_release,
    ).stdout.strip()
    second_release.write_text(
        second_release.read_text(encoding="utf-8") + "EXTRA=changed\n",
        encoding="utf-8",
    )
    changed = _state(
        "desktop-digest",
        "--release-dir",
        release,
        "--app-env",
        second_app,
        "--release-env",
        second_release,
    ).stdout.strip()

    assert first == second
    assert changed != first


def test_release_manifest_rejects_foreign_owned_tree_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _ = _root(tmp_path)
    release = _release(root, tmp_path / "unused-marker")
    victim = release / "scripts" / "probe.sh"
    release_state = _release_state_module()
    original_lstat = Path.lstat

    def foreign_owned_lstat(path: Path):
        metadata = original_lstat(path)
        if path == victim:
            values = list(metadata)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return metadata

    monkeypatch.setattr(Path, "lstat", foreign_owned_lstat)

    with pytest.raises(
        release_state.StateError,
        match="release tree is not launcher-owned",
    ):
        release_state.verify_release_manifest(
            release,
            require_read_only=True,
        )


@pytest.mark.parametrize(
    ("state", "fixture"),
    (("app", _app_fixture), ("desktop", _desktop_fixture)),
)
def test_stable_verifier_executes_only_exact_cryptographic_active_state(
    tmp_path: Path,
    state: str,
    fixture,
) -> None:
    verifier, root, marker, state_dir = fixture(tmp_path)

    result = _run(verifier, root, state, "expected")

    assert result.returncode == 0, result.stderr
    value, release_dir, observed_state, observed_app_state = (
        marker.read_text(encoding="utf-8").strip().split("|")
    )
    assert value == "expected"
    assert Path(release_dir) == root / "releases" / "release-1"
    assert Path(observed_state) == state_dir
    expected_app_state = (
        state_dir if state == "app" else (root / "shared" / "active-state").resolve(strict=True)
    )
    assert Path(observed_app_state) == expected_app_state


@pytest.mark.parametrize(
    "target",
    ("app.env", "release-images.env", "state.json", "color"),
)
def test_tampered_application_state_is_rejected_before_entrypoint_exec(
    tmp_path: Path,
    target: str,
) -> None:
    verifier, root, marker, state_dir = _app_fixture(tmp_path)
    path = state_dir / target
    if target == "state.json":
        document = json.loads(path.read_text(encoding="utf-8"))
        document["prepared_at"] = "2000-01-01T00:00:00+00:00"
        path.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif target == "color":
        path.write_text("green\n", encoding="utf-8")
    else:
        path.write_text(
            path.read_text(encoding="utf-8") + "TAMPERED=true\n",
            encoding="utf-8",
        )
    path.chmod(0o600)

    result = _run(verifier, root, "app", "must-not-run")

    assert result.returncode == 70
    assert "active app state verification failed" in result.stderr
    assert not marker.exists()


def test_renamed_application_state_with_rewritten_metadata_is_rejected(
    tmp_path: Path,
) -> None:
    verifier, root, marker, state_dir = _app_fixture(tmp_path)
    renamed_state = state_dir.with_name("release-1-blue-renamed")
    state_dir.rename(renamed_state)
    metadata_path = renamed_state / "state.json"
    document = json.loads(metadata_path.read_text(encoding="utf-8"))
    document["state_id"] = renamed_state.name
    document["prepared_at"] = "2000-01-01T00:00:00+00:00"
    metadata_path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    metadata_path.chmod(0o600)
    active_pointer = root / "shared" / "active-state"
    active_pointer.unlink()
    active_pointer.symlink_to(Path("active-states") / renamed_state.name)

    result = _run(verifier, root, "app", "must-not-run")

    assert result.returncode == 70
    assert "active app state verification failed" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    "target",
    ("app.env", "release-images.env", "fingerprint"),
)
def test_tampered_desktop_state_is_rejected_before_entrypoint_exec(
    tmp_path: Path,
    target: str,
) -> None:
    verifier, root, marker, state_dir = _desktop_fixture(tmp_path)
    path = state_dir / target
    if target == "fingerprint":
        path.write_text(f"{'0' * 64}\n", encoding="ascii")
    else:
        path.write_text(
            path.read_text(encoding="utf-8") + "TAMPERED=true\n",
            encoding="utf-8",
        )
    path.chmod(0o600)

    result = _run(verifier, root, "desktop", "must-not-run")

    assert result.returncode == 70
    assert "active desktop state verification failed" in result.stderr
    assert not marker.exists()


def test_desktop_verifier_rejects_missing_app_dependency_cleanly(
    tmp_path: Path,
) -> None:
    verifier, root, marker, _ = _desktop_fixture(tmp_path)
    (root / "shared" / "active-state").unlink()

    result = _run(verifier, root, "desktop", "must-not-run")

    assert result.returncode == 70
    assert "active-state must be a symlink" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    ("state", "fixture"),
    (("app", _app_fixture), ("desktop", _desktop_fixture)),
)
def test_repointed_state_release_is_rejected_before_entrypoint_exec(
    tmp_path: Path,
    state: str,
    fixture,
) -> None:
    verifier, root, marker, state_dir = fixture(tmp_path)
    original = root / "releases" / "release-1"
    alternate = root / "releases" / "release-2"
    shutil.copytree(original, alternate, symlinks=True)
    release_pointer = state_dir / "release"
    release_pointer.unlink()
    release_pointer.symlink_to(alternate)

    result = _run(verifier, root, state, "must-not-run")

    assert result.returncode == 70
    assert f"active {state} state verification failed" in result.stderr
    assert not marker.exists()


def test_active_pointer_change_during_state_verification_is_rejected(
    tmp_path: Path,
) -> None:
    verifier, root, marker, initial_state = _app_fixture(tmp_path)
    shared = root / "shared"
    race_app_env = _write(shared / "race.app.env", "VALUE=race\n")
    race_release_env = _write(
        shared / "race.release.env",
        _release_env(),
    )
    race_state = Path(
        _state(
            "prepare",
            "--state-root",
            shared,
            "--release-root",
            root / "releases",
            "--release-dir",
            root / "releases" / "release-1",
            "--app-env",
            race_app_env,
            "--release-env",
            race_release_env,
            "--release-id",
            "release-1",
            "--color",
            "green",
        ).stdout.strip()
    )
    assert race_state != initial_state
    _install_active_state_flip_wrapper(
        verifier,
        target_state=race_state,
    )

    result = _run(verifier, root, "app", "must-not-run")

    assert result.returncode == 70
    assert "state changed during cryptographic verification" in result.stderr
    assert not marker.exists()


def test_desktop_consumer_keeps_verified_app_state_after_pointer_flip(
    tmp_path: Path,
) -> None:
    verifier, root, marker, desktop_state = _desktop_fixture(tmp_path)
    shared = root / "shared"
    release = root / "releases" / "release-1"
    initial_app_state = (shared / "active-state").resolve(strict=True)
    race_app_env = _write(shared / "post-verify.app.env", "VALUE=race\n")
    race_release_env = _write(
        shared / "post-verify.release.env",
        _release_env(),
    )
    race_state = Path(
        _state(
            "prepare",
            "--state-root",
            shared,
            "--release-root",
            root / "releases",
            "--release-dir",
            release,
            "--app-env",
            race_app_env,
            "--release-env",
            race_release_env,
            "--release-id",
            "release-1",
            "--color",
            "green",
        ).stdout.strip()
    )
    assert race_state != initial_app_state

    result = subprocess.run(
        [
            str(verifier),
            "--root",
            str(root),
            "--state",
            "desktop",
            "--entrypoint",
            "scripts/pinned-state-probe.sh",
            "--",
            str(marker),
            race_state.name,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    pinned_value, live_value, observed_desktop, observed_app = (
        marker.read_text(encoding="utf-8").strip().split("|")
    )
    assert pinned_value == "safe"
    assert live_value == "race"
    assert Path(observed_desktop) == desktop_state
    assert Path(observed_app) == initial_app_state
    assert (shared / "active-state").resolve(strict=True) == race_state


def test_tampered_active_release_is_rejected_before_entrypoint_exec(
    tmp_path: Path,
) -> None:
    verifier, root, marker, _ = _app_fixture(tmp_path)
    entrypoint = root / "releases" / "release-1" / "scripts" / "probe.sh"
    entrypoint.chmod(0o755)
    entrypoint.write_text(
        "#!/usr/bin/env bash\nexit 99\n",
        encoding="utf-8",
    )
    entrypoint.chmod(0o555)

    result = _run(verifier, root, "app", "must-not-run")

    assert result.returncode == 70
    assert "active release manifest verification failed" in result.stderr
    assert not marker.exists()


def test_writable_active_release_is_rejected_before_entrypoint_exec(
    tmp_path: Path,
) -> None:
    verifier, root, marker, _ = _app_fixture(tmp_path)
    release = root / "releases" / "release-1"
    release.chmod(0o700)

    result = _run(verifier, root, "app", "must-not-run")

    assert result.returncode == 70
    assert "release tree is writable" in result.stderr
    assert not marker.exists()


def test_installer_and_systemd_use_the_stable_root_owned_contract() -> None:
    installer = (ROOT / "scripts/install-release-reconciler.sh").read_text(encoding="utf-8")
    reconciler = (ROOT / "scripts/reconcile-platform-release.sh").read_text(encoding="utf-8")
    verifier = VERIFIER_SCRIPT.read_text(encoding="utf-8")
    desktop_release = (ROOT / "scripts/platform-desktop-release.sh").read_text(encoding="utf-8")
    desktop_transaction = (ROOT / "scripts/platform-desktop-transaction.sh").read_text(
        encoding="utf-8"
    )
    verifier_path = "/usr/local/libexec/fb-agent-release-verifier/current/verified-release-exec.py"

    assert "VERIFIER_INSTALL_ROOT" in installer
    assert "manifest-verify" in installer
    assert "--require-read-only" in installer
    assert "install -o root -g root -m 0555" in installer
    assert "install -o root -g root -m 0444" in installer
    assert 'mv -Tf -- "$VERIFIER_LINK"' in installer
    assert '"runtime-verify"' in verifier
    assert verifier.index("_verify_state_contract(") < verifier.index(
        "_verify_release(state_verifier, release_dir)"
    )
    assert "verified_state_dir != state_dir" in verifier
    assert "verified_release_dir != release_dir" in verifier
    assert 'release-state.py" desktop-digest' in desktop_release
    assert 'release-state.py" desktop-verify' in desktop_release
    assert 'release-state.py" desktop-verify' in desktop_transaction
    deploy = (ROOT / "scripts/deploy-platform-server.sh").read_text(encoding="utf-8")
    assert "chown -hR root:root '$REMOTE_STAGING'" in deploy
    assert reconciler.index('"verify_active_release"') < reconciler.index(
        '"restore_committed_caddy"'
    )
    for unit in (
        "fb-agent.service",
        "fb-agent-alloy-agent.service",
        "fb-agent-healthcheck.service",
        "fb-agent-pgbackrest-full.service",
        "fb-agent-pgbackrest-diff.service",
        "fb-agent-restore-drill.service",
    ):
        source = (ROOT / "deploy/systemd" / unit).read_text(encoding="utf-8")
        assert verifier_path in source
        assert "/opt/fb-agent/current/scripts/" not in source
