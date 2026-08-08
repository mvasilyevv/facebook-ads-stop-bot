from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "fb-agent-verified-release-exec/v1"
CLUSTER_ID = "b" * 32
IMAGE = "registry.example/fb-agent@sha256:" + "a" * 64


def _write(path: Path, content: str, *, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _runtime_environment(
    tmp_path: Path,
    runtime: str,
) -> tuple[dict[str, str], Path, Path, Path, Path]:
    root = tmp_path / "root"
    shared = root / "shared"
    states = shared / "active-states"
    pinned_state = states / "release-a"
    replacement_state = states / "release-b"
    for state, release_id in (
        (pinned_state, "release-a"),
        (replacement_state, "release-b"),
    ):
        state.mkdir(parents=True)
        _write(
            state / "app.env",
            f"FB_AGENT_BOOTSTRAP_CLUSTER_ID={CLUSTER_ID}\n",
        )
        _write(
            state / "release-images.env",
            f"RELEASE_ID={release_id}\n",
        )
        _write(state / "color", "blue\n")
    active_link = shared / "active-state"
    active_link.symlink_to(Path("active-states") / pinned_state.name)

    _write(shared / "pgbackrest.env", "PGBACKREST_STANZA=fb_agent\n")
    _write(shared / "pgbackrest.conf", "[global]\n")
    _write(
        shared / "browser-control.env",
        (
            f"BROWSER_MAINTENANCE_CAPABILITY_SECRET={'c' * 64}\n"
            f"BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE={'a' * 64}\n"
            f"BROWSER_OPERATION_CAPABILITY_SECRET_META_API={'m' * 64}\n"
            "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR="
            f"{'r' * 64}\n"
            f"BROWSER_AUTHORITY_CONSUMER_TOKEN={'t' * 64}\n"
        ),
    )
    _write(
        shared / "browser-maintenance.env",
        f"BROWSER_MAINTENANCE_CAPABILITY_SECRET={'c' * 64}\n",
    )
    _write(
        shared / "browser-autopause.env",
        f"BROWSER_OPERATION_CAPABILITY_SECRET={'a' * 64}\n",
    )
    _write(
        shared / "browser-meta-api.env",
        f"BROWSER_OPERATION_CAPABILITY_SECRET={'m' * 64}\n",
    )
    _write(
        shared / "browser-campaign-creator.env",
        f"BROWSER_OPERATION_CAPABILITY_SECRET={'r' * 64}\n",
    )
    _write(
        shared / "browser-authority.env",
        f"BROWSER_AUTHORITY_CONSUMER_TOKEN={'t' * 64}\n",
    )
    _write(
        shared / "alloy-agent.env",
        (
            f"ALLOY_IMAGE={IMAGE}\n"
            f"NODE_EXPORTER_IMAGE={IMAGE}\n"
            f"CADVISOR_IMAGE={IMAGE}\n"
            "PLATFORM_NETWORK=fb_agent_safety_first_platform\n"
            "PROMETHEUS_READY_URL=https://monitor.example/-/ready\n"
            "LOKI_READY_URL=https://monitor.example/loki/ready\n"
            "TEMPO_READY_URL=https://monitor.example/tempo/ready\n"
        ),
    )

    release = root / "releases" / "release-a"
    _write(release / "deploy/compose/docker-compose.app.yml", "services: {}\n")
    _write(release / "deploy/compose/docker-compose.infra.yml", "services: {}\n")
    _write(
        release / "deploy/monitoring/docker-compose.agent.yml",
        "services: {}\n",
    )
    _write(release / "release-images.env", "RELEASE_ID=release-a\n")
    _write(
        release / "scripts/platform-network-inventory.py",
        "raise SystemExit(0)\n",
        mode=0o755,
    )
    _write(
        release / "scripts/browser-control-env.sh",
        (ROOT / "scripts/browser-control-env.sh").read_text(encoding="utf-8"),
        mode=0o755,
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    flock_log = tmp_path / "flock.log"
    _write(
        fake_bin / "python3",
        '#!/usr/bin/env bash\nexec "$TEST_PYTHON" "$@"\n',
        mode=0o755,
    )
    _write(
        fake_bin / "stat",
        (
            "#!/usr/bin/env bash\n"
            'if [[ "$*" == *"%a:%u"* ]]; then\n'
            "  printf '600:%s\\n' \"$(id -u)\"\n"
            "  exit 0\n"
            "fi\n"
            'if [[ "$*" == *"%a"* ]]; then printf \'600\\n\'; exit 0; fi\n'
            'exec /usr/bin/stat "$@"\n'
        ),
        mode=0o755,
    )
    _write(
        fake_bin / "curl",
        (
            "#!/usr/bin/env bash\n"
            'url="${!#}"\n'
            "if [[ \"$url\" == *'/system-readyz' ]]; then\n"
            "  printf '%s\\n' "
            '\'{"infrastructure_ready":true,"actors_expected":1,'
            '"actors_active":1,"stale_money_tasks":0,'
            '"expired_money_tasks":0,"blockers":[],'
            '"scanning_enabled":true}\'\n'
            'elif [[ "$*" == *"--write-out"* ]]; then\n'
            "  printf '200'\n"
            "fi\n"
        ),
        mode=0o755,
    )
    _write(
        fake_bin / "docker",
        (
            "#!/usr/bin/env bash\n"
            'printf \'%s\\n\' "$*" >>"$DOCKER_LOG"\n'
            "if [[ \"$*\" == 'compose version' ]]; then exit 0; fi\n"
            'if [[ "$1" == inspect && "$*" == *\'.State.Running\'* ]]; then\n'
            "  printf '%s\\n' 'true|release-a'\n"
            'elif [[ "$1" == inspect ]]; then\n'
            "  printf '%s\\n' 'release-a'\n"
            "elif [[ \"$*\" == *'ps --status running --services'* ]]; then\n"
            "  printf '%s\\n' alloy-agent node-exporter cadvisor\n"
            "elif [[ \"$*\" == *' ps -q '* ]]; then\n"
            "  printf '%s\\n' 'container-id'\n"
            "fi\n"
        ),
        mode=0o755,
    )
    _write(
        fake_bin / "flock",
        (
            "#!/usr/bin/env bash\n"
            'printf \'%s\\n\' "$*" >>"$FLOCK_LOG"\n'
            '"$TEST_PYTHON" - "$@" <<\'PY\'\n'
            "import fcntl\n"
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n"
            "\n"
            "descriptor = int(sys.argv[-1])\n"
            "fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "target = os.environ.get('FLIP_ACTIVE_TO')\n"
            "if target:\n"
            "    active = Path(os.environ['ACTIVE_STATE_LINK'])\n"
            "    temporary = active.with_name(f'.active-state-test-{os.getpid()}')\n"
            "    temporary.unlink(missing_ok=True)\n"
            "    temporary.symlink_to(os.path.relpath(target, active.parent))\n"
            "    os.replace(temporary, active)\n"
            "PY\n"
        ),
        mode=0o755,
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "TEST_PYTHON": sys.executable,
        "DOCKER_LOG": str(docker_log),
        "FLOCK_LOG": str(flock_log),
        "ACTIVE_STATE_LINK": str(active_link),
        "FB_AGENT_ROOT": str(root),
        "FB_AGENT_RELEASE_DIR": str(release),
        "FB_AGENT_VERIFIED_RELEASE_SCHEMA": SCHEMA,
        "FB_AGENT_ACTIVE_STATE_DIR": str(pinned_state),
    }
    script = ROOT / "scripts" / runtime
    return env, script, active_link, replacement_state, docker_log


def _replace_active_state(active_link: Path, target: Path) -> None:
    temporary = active_link.with_name(".active-state-test-replacement")
    temporary.symlink_to(os.path.relpath(target, active_link.parent))
    os.replace(temporary, active_link)


def _mutation_lines(docker_log: Path) -> list[str]:
    return [
        line
        for line in docker_log.read_text(encoding="utf-8").splitlines()
        if line != "compose version"
    ]


@pytest.mark.parametrize(
    "runtime",
    ("platform-compose.sh", "platform-alloy-agent.sh"),
)
@pytest.mark.parametrize("change_point", ("before_lock", "while_locking"))
def test_verified_runtime_rejects_a_changed_active_snapshot_before_mutation(
    tmp_path: Path,
    runtime: str,
    change_point: str,
) -> None:
    env, script, active_link, replacement_state, docker_log = _runtime_environment(
        tmp_path, runtime
    )
    if change_point == "before_lock":
        _replace_active_state(active_link, replacement_state)
    else:
        env["FLIP_ACTIVE_TO"] = str(replacement_state)

    result = subprocess.run(
        ["bash", str(script), "stop"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "active application state changed after verification" in result.stderr
    assert _mutation_lines(docker_log) == []


@pytest.mark.parametrize(
    "runtime",
    ("platform-compose.sh", "platform-alloy-agent.sh"),
)
def test_unverified_release_path_accepts_the_inherited_release_lock(
    tmp_path: Path,
    runtime: str,
) -> None:
    env, script, _active_link, _replacement_state, docker_log = _runtime_environment(
        tmp_path, runtime
    )
    env.pop("FB_AGENT_VERIFIED_RELEASE_SCHEMA")
    env.pop("FB_AGENT_ACTIVE_STATE_DIR")
    lock_fd = os.open(
        Path(env["FB_AGENT_ROOT"]) / "shared" / "deploy.lock",
        os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env["FB_AGENT_DEPLOY_LOCK_FD"] = str(lock_fd)
        result = subprocess.run(
            ["bash", str(script), "stop"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            pass_fds=(lock_fd,),
        )
    finally:
        os.close(lock_fd)

    assert result.returncode == 0, result.stderr
    assert any(" stop " in f" {line} " for line in _mutation_lines(docker_log))


@pytest.mark.parametrize(
    "runtime",
    ("platform-compose.sh", "platform-alloy-agent.sh"),
)
def test_unverified_runtime_rejects_mutation_without_an_inherited_lock(
    tmp_path: Path,
    runtime: str,
) -> None:
    env, script, _active_link, _replacement_state, docker_log = _runtime_environment(
        tmp_path, runtime
    )
    env.pop("FB_AGENT_VERIFIED_RELEASE_SCHEMA")
    env.pop("FB_AGENT_ACTIVE_STATE_DIR")

    result = subprocess.run(
        ["bash", str(script), "stop"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "verified launcher or inherited deployment lock" in result.stderr
    assert _mutation_lines(docker_log) == []


@pytest.mark.parametrize(
    "runtime",
    ("platform-compose.sh", "platform-alloy-agent.sh"),
)
def test_verified_restart_reuses_the_held_lock_for_nested_up(
    tmp_path: Path,
    runtime: str,
) -> None:
    env, script, _active_link, _replacement_state, _docker_log = _runtime_environment(
        tmp_path, runtime
    )

    result = subprocess.run(
        ["bash", str(script), "restart"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    flock_calls = Path(env["FLOCK_LOG"]).read_text(encoding="utf-8").splitlines()
    assert flock_calls == ["-n 9", "-n 9"]


def test_verified_mutation_guards_cover_all_runtime_mutators() -> None:
    platform = (ROOT / "scripts/platform-compose.sh").read_text(encoding="utf-8")
    alloy = (ROOT / "scripts/platform-alloy-agent.sh").read_text(encoding="utf-8")
    release = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")

    assert "up|stop|restart|compose|infra) acquire_runtime_mutation_lock" in platform
    assert "up|candidate-up|candidate-cleanup|promote|stop|restart|compose)" in alloy
    exported_lock = release.index("export FB_AGENT_DEPLOY_LOCK_FD=9")
    assert exported_lock < release.index('"$SCRIPT_DIR/platform-alloy-agent.sh" candidate-up')
    assert exported_lock < release.index("ALLOY_CANDIDATE_STARTED=true")
    for source in (platform, alloy):
        guard = source.split("acquire_runtime_mutation_lock() {", maxsplit=1)[1].split(
            "\n}\n", maxsplit=1
        )[0]
        assert "FB_AGENT_DEPLOY_LOCK_FD" in guard
        assert "os.fstat" in guard
        assert "flock -n" in guard
        assert "verified launcher or inherited deployment lock" in guard
        assert guard.rindex("flock -n") < guard.index("$STATE_DIR/active-state")
        assert guard.index("$STATE_DIR/active-state") < guard.index('"$APP_STATE_DIR"')
