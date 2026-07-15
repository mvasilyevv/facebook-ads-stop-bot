import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_vision_failure_does_not_trigger_application_rollback() -> None:
    script = (ROOT / "scripts" / "server-release.sh").read_text()

    vision_block = script.split("if ! ensure_cdp_ready; then", maxsplit=1)[1].split(
        "fi", maxsplit=1
    )[0]
    assert "false" not in vision_block
    assert "safe degraded state" in vision_block


def test_application_rollback_never_runs_previous_migrations() -> None:
    script = (ROOT / "scripts" / "server-release.sh").read_text()

    rollback_block = script.split("rollback() {", maxsplit=1)[1].split(
        "trap rollback ERR", maxsplit=1
    )[0]
    assert "postgres|redis|migrate" in rollback_block
    assert "--no-deps" in rollback_block
    assert "config --services" in rollback_block


def test_active_deploy_is_not_cancelled_by_new_push() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    assert "cancel-in-progress: false" in workflow


def test_release_waits_for_all_worker_heartbeats() -> None:
    script = (ROOT / "scripts" / "server-release.sh").read_text()
    assert "ensure_worker_heartbeats_ready" in script
    assert "workers_expected" in script
    assert "workers_online" in script


def test_release_discards_previous_release_heartbeats_before_acceptance() -> None:
    script = (ROOT / "scripts" / "server-release.sh").read_text()
    clear_pos = script.rindex("clear_worker_heartbeats\n")
    wait_pos = script.rindex("ensure_worker_heartbeats_ready\n")

    assert "redis-cli --raw --scan --pattern 'worker:heartbeat:*'" in script
    assert "redis-cli DEL" in script
    assert clear_pos < wait_pos


def test_heartbeat_scan_failure_aborts_release() -> None:
    script = (ROOT / "scripts" / "server-release.sh").read_text()
    function = (
        "clear_worker_heartbeats() {"
        + script.split("clear_worker_heartbeats() {", maxsplit=1)[1].split(
            "\n}\n\nif [[", maxsplit=1
        )[0]
        + "\n}"
    )
    harness = f"""
set -Eeuo pipefail
compose=(mock_compose)
mock_compose() {{
  if [[ "$*" == *"--scan"* ]]; then
    return 37
  fi
  return 0
}}
{function}
clear_worker_heartbeats
"""

    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "failed to scan stale worker heartbeats" in result.stderr
    assert "Cleared stale worker heartbeats" not in result.stdout


def test_manual_ready_allows_intentional_scanning_pause_but_requires_workers() -> None:
    script = (ROOT / "scripts" / "server-compose.sh").read_text()

    assert "curl --silent --show-error --max-time 10 http://127.0.0.1:8100/system-readyz" in script
    assert 'd.get("infrastructure_ready")' in script
    assert "online == expected" in script


def test_systemd_waits_for_vision_namespace_before_app_compose() -> None:
    expected = "ExecStartPre=/opt/fb-agent/current/scripts/wait-for-vision-container.sh"
    for relative_path in (
        "deploy/systemd/fb-agent.service",
        "deploy/fb-agent.service",
    ):
        unit = (ROOT / relative_path).read_text()
        assert expected in unit
        assert unit.index("ExecStartPre=") < unit.index("ExecStart=")


def test_allow_vision_offline_never_bypasses_missing_namespace() -> None:
    script = (ROOT / "scripts" / "server-preflight.sh").read_text()
    namespace_gate = script.index("if ! vision_container_running; then")
    api_gate = script.index("if ! vision_api_ready; then")

    assert namespace_gate < api_gate
    assert "--allow-vision-offline cannot bypass this" in script
    assert "Vision namespace is running but API" in script


def test_vision_namespace_wait_script_accepts_running_container(tmp_path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/usr/bin/env bash\nprintf 'true\\n'\n")
    docker.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "VISION_WAIT_TIMEOUT_SECONDS": "0",
    }

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "wait-for-vision-container.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert "Vision namespace container is running" in result.stdout


def test_vision_namespace_wait_script_fails_closed_at_timeout(tmp_path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/usr/bin/env bash\nprintf 'false\\n'\n")
    docker.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "VISION_WAIT_TIMEOUT_SECONDS": "0",
        "VISION_WAIT_INTERVAL_SECONDS": "0",
    }

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "wait-for-vision-container.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "did not become running" in result.stderr
