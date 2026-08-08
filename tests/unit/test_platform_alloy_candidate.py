from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "scripts/platform-alloy-agent.sh"
COMPOSE = ROOT / "deploy/monitoring/docker-compose.agent.yml"


def _runtime_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    root = tmp_path / "fb-agent"
    shared = root / "shared"
    active_state = shared / "active-states/current"
    previous = root / "releases/previous"
    previous_compose = previous / "deploy/monitoring/docker-compose.agent.yml"
    active_state.mkdir(parents=True)
    (shared / "active-state").symlink_to(Path("active-states/current"))
    previous_compose.parent.mkdir(parents=True)
    shutil.copyfile(COMPOSE, previous_compose)
    (previous / "release-images.env").write_text(
        "RELEASE_ID=previous\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "python3",
            str(ROOT / "scripts/release-state.py"),
            "manifest-write",
            "--release-dir",
            str(previous),
            "--manifest",
            str(previous / ".fb-agent-source-manifest.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for directory, directory_names, file_names in os.walk(previous, topdown=False):
        directory_path = Path(directory)
        for name in file_names:
            path = directory_path / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
        for name in directory_names:
            path = directory_path / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
    previous.chmod(previous.stat().st_mode & ~0o222)

    digest = "1" * 64
    agent_env = shared / "alloy-agent.env"
    agent_env.write_text(
        "\n".join(
            (
                "NODE_NAME=test-node",
                f"ALLOY_IMAGE=example/alloy@sha256:{digest}",
                f"NODE_EXPORTER_IMAGE=example/node-exporter@sha256:{digest}",
                f"CADVISOR_IMAGE=example/cadvisor@sha256:{digest}",
                "PROMETHEUS_REMOTE_WRITE_URL=https://monitoring.test/prometheus/api/v1/write",
                "LOKI_WRITE_URL=https://monitoring.test/loki/api/v1/push",
                "TEMPO_OTLP_HTTP_URL=https://monitoring.test/otlp",
                "PROMETHEUS_READY_URL=https://monitoring.test/prometheus/-/ready",
                "LOKI_READY_URL=https://monitoring.test/loki/ready",
                "TEMPO_READY_URL=https://monitoring.test/tempo/ready",
                "",
            )
        ),
        encoding="utf-8",
    )
    agent_env.chmod(0o600)

    calls = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
printf 'DOCKER' >>"$CALL_LOG"
printf ' %q' "$@" >>"$CALL_LOG"
printf '\\n' >>"$CALL_LOG"
if [[ "${1:-}" == compose && "${2:-}" == version ]]; then
  exit 0
fi
if [[ "${1:-}" == inspect ]]; then
  container_id="${*: -1}"
  if [[ "$container_id" == container-fb_agent_telemetry_agent \
    && ! -f "$CANONICAL_MARKER" ]]; then
    printf 'previous\\n'
  else
    printf '%s\\n' "${FB_AGENT_TELEMETRY_RELEASE_ID:?}"
  fi
  exit 0
fi
project=""
compose_file=""
previous=""
for argument in "$@"; do
  if [[ "$previous" == -p ]]; then project="$argument"; fi
  if [[ "$previous" == -f ]]; then compose_file="$argument"; fi
  previous="$argument"
done
if [[ " $* " == *" ps --status running --services "* ]]; then
  printf 'alloy-agent\\nnode-exporter\\ncadvisor\\n'
  exit 0
fi
if [[ " $* " == *" ps -q "* ]]; then
  printf 'container-%s\\n' "$project"
  exit 0
fi
if [[ " $* " == *" config --images "* ]]; then
  printf 'example/alloy@sha256:%064d\\n' 1
  exit 0
fi
if [[ "${FAIL_CANONICAL_NEW:-0}" == 1 \
  && "$project" == fb_agent_telemetry_agent \
  && "$compose_file" == "$NEW_COMPOSE_FILE" \
  && " $* " == *" up -d "* ]]; then
  exit 42
fi
if [[ "$project" == fb_agent_telemetry_agent \
  && "$compose_file" == "$NEW_COMPOSE_FILE" \
  && " $* " == *" up -d "* ]]; then
  : >"$CANONICAL_MARKER"
fi
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -eu
url="${*: -1}"
if [[ "$url" == https://* ]]; then
  printf '%s' "${REMOTE_READY_STATUS:-204}"
else
  printf '%s' "${LOCAL_READY_STATUS:-200}"
fi
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)
    stat = bin_dir / "stat"
    stat.write_text("#!/usr/bin/env bash\nprintf '600\\n'\n", encoding="utf-8")
    stat.chmod(0o755)
    readlink = bin_dir / "readlink"
    readlink.write_text(
        """#!/usr/bin/env bash
set -eu
[[ "${1:-}" == -f ]]
python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$2"
""",
        encoding="utf-8",
    )
    readlink.chmod(0o755)
    flock = bin_dir / "flock"
    flock.write_text(
        """#!/usr/bin/env bash
set -eu
descriptor="${@: -1}"
"$TEST_PYTHON" - "$descriptor" <<'PY'
import fcntl
import sys

fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
PY
""",
        encoding="utf-8",
    )
    flock.chmod(0o755)

    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FB_AGENT_ROOT": str(root),
        "FB_AGENT_RELEASE_DIR": str(ROOT),
        "CALL_LOG": str(calls),
        "NEW_COMPOSE_FILE": str(COMPOSE),
        "CANONICAL_MARKER": str(tmp_path / "canonical-current"),
        "FB_AGENT_RELEASE_ID": "current",
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID": "a" * 32,
        "FB_AGENT_VERIFIED_RELEASE_SCHEMA": "fb-agent-verified-release-exec/v1",
        "FB_AGENT_ACTIVE_STATE_DIR": str(active_state),
        "TEST_PYTHON": sys.executable,
    }
    return env, calls, previous


def test_failed_candidate_preflight_never_addresses_incumbent_and_cleans_up(
    tmp_path: Path,
) -> None:
    env, calls, _previous = _runtime_environment(tmp_path)
    env["REMOTE_READY_STATUS"] = "503"

    result = subprocess.run(
        ["bash", str(RUNTIME), "candidate-up"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "candidate was removed" in result.stderr
    log = calls.read_text(encoding="utf-8")
    assert "-p fb_agent_telemetry_candidate" in log
    assert " down --volumes --remove-orphans" in log
    assert "-p fb_agent_telemetry_agent" not in log
    assert " pull " not in log
    assert " up -d " not in log


def test_failed_post_commit_promotion_restores_incumbent_and_removes_candidate(
    tmp_path: Path,
) -> None:
    env, calls, previous = _runtime_environment(tmp_path)
    env["FAIL_CANONICAL_NEW"] = "1"

    result = subprocess.run(
        [
            "bash",
            str(RUNTIME),
            "promote",
            "--previous-release-dir",
            str(previous),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "incumbent was restored and candidate removed" in result.stderr
    log = calls.read_text(encoding="utf-8")
    candidate_ready = log.index("-p fb_agent_telemetry_candidate")
    previous_compose = f"-f {previous}/deploy/monitoring/docker-compose.agent.yml"
    previous_config = log.index(previous_compose, candidate_ready)
    canonical_down = log.index(" down --remove-orphans", previous_config)
    restore_config = log.index(previous_compose, canonical_down)
    previous_up = log.index(" up -d ", restore_config)
    candidate_cleanup = log.rindex("-p fb_agent_telemetry_candidate")
    assert (
        candidate_ready
        < previous_config
        < canonical_down
        < restore_config
        < previous_up
        < candidate_cleanup
    )
    assert " down --volumes --remove-orphans" in log[candidate_cleanup:]


def test_alloy_compose_has_distinct_candidate_port_and_alias_controls() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")

    assert "${ALLOY_AGENT_HOST_PORT:-12345}" in compose
    assert "${ALLOY_AGENT_NETWORK_ALIAS:-alloy-agent}" in compose
    assert "fb_agent_telemetry_candidate" in runtime
    assert "22345" in runtime
    assert "alloy-agent-candidate" in runtime
    assert '[[ "$status" =~ ^2[0-9][0-9]$ ]]' in runtime
    assert "^[1-5][0-9][0-9]$" not in runtime
