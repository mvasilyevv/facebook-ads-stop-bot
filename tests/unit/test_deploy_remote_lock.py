from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "scripts/deploy-platform-server.sh"
REMOTE_LOCK = ROOT / "scripts/deploy-lock-remote.sh"

OWNER_ONE = "1" * 32
OWNER_TWO = "2" * 32


def _fake_command(bin_dir: Path, name: str, body: str = "exit 0") -> None:
    command = bin_dir / name
    command.write_text(
        f"#!/usr/bin/env bash\nset -eu\n{body}\n",
        encoding="utf-8",
    )
    command.chmod(0o755)


def _lock_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_command(
        bin_dir,
        "flock",
        '[[ "$1" == "-x" && "$2" == "-w" && "$3" == "10" && "$4" == "9" ]]',
    )
    _fake_command(bin_dir, "timeout")
    return os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _run_lock(
    action: str,
    root: Path,
    owner: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(REMOTE_LOCK), action, str(root), owner, "90"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _run_publish(
    root: Path,
    owner: str,
    source: Path,
    target: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(REMOTE_LOCK),
            "publish",
            str(root),
            owner,
            "90",
            str(source),
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_expired_remote_lease_is_recoverable_and_release_is_owner_cas(
    tmp_path: Path,
) -> None:
    root = tmp_path / "deployment"
    shared = root / "shared"
    shared.mkdir(parents=True, mode=0o700)
    shared.chmod(0o700)
    production_env = shared / ".env"
    production_env.write_text("POSTGRES_DB=fb_agent\n", encoding="utf-8")
    production_env.chmod(0o600)
    env = _lock_env(tmp_path)

    acquired = _run_lock("acquire", root, OWNER_ONE, env)
    assert acquired.returncode == 0, acquired.stderr
    lock_dir = shared / ".platform-deploy.lock"
    owner_file = lock_dir / "owner"
    expiry_file = lock_dir / "expires_at"
    assert owner_file.read_text(encoding="utf-8").strip() == OWNER_ONE

    active_takeover = _run_lock("acquire", root, OWNER_TWO, env)
    assert active_takeover.returncode != 0
    assert owner_file.read_text(encoding="utf-8").strip() == OWNER_ONE

    wrong_release = _run_lock("release", root, OWNER_TWO, env)
    assert wrong_release.returncode != 0
    assert owner_file.read_text(encoding="utf-8").strip() == OWNER_ONE

    expiry_file.write_text("1\n", encoding="utf-8")
    expiry_file.chmod(0o600)
    takeover = _run_lock("acquire", root, OWNER_TWO, env)
    assert takeover.returncode == 0, takeover.stderr
    assert owner_file.read_text(encoding="utf-8").strip() == OWNER_TWO

    delayed_old_cleanup = _run_lock("release", root, OWNER_ONE, env)
    assert delayed_old_cleanup.returncode != 0
    assert owner_file.read_text(encoding="utf-8").strip() == OWNER_TWO
    assert _run_lock("assert", root, OWNER_TWO, env).returncode == 0

    staging = root / "releases" / ".incoming-release-ABCDEFGH"
    staging.mkdir(mode=0o700)
    release = root / "releases" / "release"
    stale_publish = _run_publish(root, OWNER_ONE, staging, release, env)
    assert stale_publish.returncode != 0
    assert staging.is_dir()
    assert not release.exists()
    published = _run_publish(root, OWNER_TWO, staging, release, env)
    assert published.returncode == 0, published.stderr
    assert release.is_dir()
    assert not staging.exists()

    released = _run_lock("release", root, OWNER_TWO, env)
    assert released.returncode == 0, released.stderr
    assert not lock_dir.exists()


def test_stale_empty_lock_from_the_retired_mkdir_protocol_is_recovered(
    tmp_path: Path,
) -> None:
    root = tmp_path / "deployment"
    shared = root / "shared"
    shared.mkdir(parents=True, mode=0o700)
    shared.chmod(0o700)
    production_env = shared / ".env"
    production_env.write_text("POSTGRES_DB=fb_agent\n", encoding="utf-8")
    production_env.chmod(0o600)
    lock_dir = shared / ".platform-deploy.lock"
    lock_dir.mkdir(mode=0o700)
    lock_dir.chmod(0o700)
    os.utime(lock_dir, (1, 1))

    acquired = _run_lock("acquire", root, OWNER_ONE, _lock_env(tmp_path))

    assert acquired.returncode == 0, acquired.stderr
    assert (lock_dir / "owner").read_text(encoding="utf-8").strip() == OWNER_ONE


def test_existing_root_symlink_is_rejected_before_creating_deploy_directories(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    root = tmp_path / "deployment"
    root.symlink_to(actual, target_is_directory=True)

    result = _run_lock("acquire", root, OWNER_ONE, _lock_env(tmp_path))

    assert result.returncode != 0
    assert "deployment root must be a regular directory" in result.stderr
    assert not (actual / "shared").exists()
    assert not (actual / "releases").exists()


@pytest.mark.parametrize(
    "unsafe_root",
    (
        "/",
        "/opt/fb-agent/",
        "/opt//fb-agent",
        "/opt/./fb-agent",
        "/opt/../fb-agent",
    ),
)
def test_deploy_rejects_noncanonical_root_before_remote_access(
    tmp_path: Path,
    unsafe_root: str,
) -> None:
    release_env = tmp_path / "release-images.env"
    release_env.write_text("\n", encoding="utf-8")
    bin_dir = tmp_path / "commands"
    bin_dir.mkdir()
    _fake_command(bin_dir, "rsync")
    _fake_command(bin_dir, "ssh")

    result = subprocess.run(
        [
            "bash",
            str(DEPLOY),
            "--host",
            "deploy@example.test",
            "--root",
            unsafe_root,
            "--release-env",
            str(release_env),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert "--root must be a canonical non-root absolute path" in result.stderr


def test_root_validation_does_not_reject_legitimate_double_dots_in_a_name(
    tmp_path: Path,
) -> None:
    release_env = tmp_path / "release-images.env"
    release_env.write_text("\n", encoding="utf-8")
    bin_dir = tmp_path / "commands"
    bin_dir.mkdir()
    _fake_command(bin_dir, "rsync")
    _fake_command(bin_dir, "ssh")

    result = subprocess.run(
        [
            "bash",
            str(DEPLOY),
            "--host",
            "deploy@example.test",
            "--root",
            "/opt/fb..agent",
            "--release-env",
            str(release_env),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert "release manifest has invalid RELEASE_ID" in result.stderr
    assert "--root must" not in result.stderr


def test_deploy_lock_contract_has_bounded_renewal_and_no_recursive_delete() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")
    remote = REMOTE_LOCK.read_text(encoding="utf-8")
    renewer = deploy.split("start_remote_lock_renewer() {", maxsplit=1)[1].split(
        "\n}\n\nassert_remote_lock_healthy()",
        maxsplit=1,
    )[0]

    assert "REMOTE_LOCK_LEASE_SECONDS=90" in deploy
    assert "REMOTE_LOCK_RENEW_INTERVAL_SECONDS=30" in deploy
    assert "secrets.token_hex(16)" in deploy
    assert "remote_lock_action acquire" in deploy
    assert "remote_lock_action renew" in deploy
    assert "remote_lock_action assert" in deploy
    assert "remote_lock_action release" in deploy
    assert 'remote_lock_publish "$REMOTE_STAGING" "$remote_release"' in deploy
    assert 'ssh "${REMOTE_LOCK_SSH_OPTIONS[@]}" "$TARGET"' in deploy
    assert "timeout 30s bash -s" in deploy
    assert '[[ "$LOCK_OWNER" == "$OWNER_TOKEN" ]]' in remote
    assert "flock -x -w 10 9" in remote
    assert "rm -rf" not in deploy
    assert "rm -rf" not in remote
    assert 'local -r parent_pid="$$"' in renewer
    assert "ps -o lstart=" in renewer
    assert '"$observed_start" == "$parent_started"' in renewer
    assert renewer.count("parent_is_exact") >= 3
    assert 'while kill -0 "$parent_pid"' not in renewer
