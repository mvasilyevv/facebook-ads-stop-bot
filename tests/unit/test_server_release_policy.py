import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VISION_IMAGE = "ghcr.io/example/vision-webtop@sha256:" + "a" * 64
BROWSER_IMAGE = "ghcr.io/example/browser-agent@sha256:" + "d" * 64
CLUSTER_ID = "b" * 32
RELEASE_ID = "release-123"
WEBTOP_ID = "1" * 64
BROWSER_ID = "3" * 64


def _desktop_state(tmp_path: Path) -> Path:
    state_root = tmp_path / "root" / "shared"
    browser_control = state_root / "browser-control.env"
    browser_control.parent.mkdir(parents=True, mode=0o700)
    browser_control.write_text(
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
        )
    )
    browser_control.chmod(0o600)
    desktop_states = state_root / "desktop-states"
    active_state = desktop_states / "committed"
    active_state.mkdir(parents=True, mode=0o700)
    active_state.chmod(0o700)
    app_env = active_state / "app.env"
    app_env.write_text(
        f"FB_AGENT_BOOTSTRAP_CLUSTER_ID={CLUSTER_ID}\n"
        "BROWSER_AUTHORITY_CONSUME_URL="
        "https://app.adpulse.su/api/v1/internal/browser-operations/consume\n"
        "BROWSER_MAINTENANCE_CONSUME_URL="
        "https://app.adpulse.su/api/v1/internal/browser-maintenance/consume\n"
    )
    app_env.chmod(0o600)
    release_env = active_state / "release-images.env"
    release_env.write_text(
        f"RELEASE_ID={RELEASE_ID}\n"
        f"DESKTOP_WEBTOP_IMAGE={VISION_IMAGE}\n"
        f"BROWSER_AGENT_IMAGE={BROWSER_IMAGE}\n"
    )
    release_env.chmod(0o600)
    (active_state / "release").symlink_to(ROOT)
    (state_root / "active-desktop-state").symlink_to(Path("desktop-states") / active_state.name)
    return active_state


def _vision_wait_environment(
    tmp_path: Path,
    observed_identity: str,
) -> dict[str, str]:
    _desktop_state(tmp_path)
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'target="${!#}"\n'
        'if [[ "$target" == "vision-webtop" && "$*" == *\'{{.Id}}\'* ]]; then\n'
        f"  printf '%s\\n' '{WEBTOP_ID}'\n"
        'elif [[ "$target" == "vision-webtop" ]]; then\n'
        f"  printf '%s\\n' '{observed_identity}'\n"
        "else\n"
        "  exit 1\n"
        "fi\n"
    )
    docker.chmod(0o755)
    return {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "FB_AGENT_ROOT": str(tmp_path / "root"),
        "VISION_WAIT_TIMEOUT_SECONDS": "0",
        "VISION_WAIT_INTERVAL_SECONDS": "0",
    }


def _vision_identity(
    *,
    cluster_id: str = CLUSTER_ID,
    image: str = VISION_IMAGE,
    release_id: str = RELEASE_ID,
    running: str = "true",
    health: str = "healthy",
) -> str:
    return (
        f"/vision-webtop|{running}|{health}|{image}|"
        f"fb_agent_vision|webtop|true|{cluster_id}|vision|{release_id}"
    )


def _run_installer_identity_check(
    *,
    cluster_id: str = CLUSTER_ID,
    image: str = VISION_IMAGE,
    release_id: str = RELEASE_ID,
    health: str = "healthy",
) -> subprocess.CompletedProcess[str]:
    installer = (ROOT / "scripts" / "install-vision-webtop.sh").read_text()
    function = installer.split("vision_identity_is_exact() {", 1)[1].split(
        "\n}\n\nassert_browser_agent_absent",
        1,
    )[0]
    inspection = f"true|{health}|{image}|true|{cluster_id}|vision|{release_id}"
    harness = f"""
compose_with_env() {{
  printf '%s\\n' '{WEBTOP_ID}'
}}
dotenv_value() {{
  case "$1" in
    DESKTOP_WEBTOP_IMAGE) printf '%s\\n' '{VISION_IMAGE}' ;;
    FB_AGENT_BOOTSTRAP_CLUSTER_ID) printf '%s\\n' '{CLUSTER_ID}' ;;
    *) return 1 ;;
  esac
}}
docker() {{
  printf '%s\\n' '{inspection}'
}}
vision_identity_is_exact() {{{function}
}}
FB_AGENT_VISION_RELEASE_ID='{RELEASE_ID}'
vision_identity_is_exact
"""
    return subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        check=False,
    )


def _browser_verify_environment(
    tmp_path: Path,
    observed_browser_identity: str,
) -> dict[str, str]:
    _desktop_state(tmp_path)
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'target="${!#}"\n'
        'if [[ "$1" == "compose" && "$*" == *\' ps -q browser-agent\'* ]]; then\n'
        f"  printf '%s\\n' '{BROWSER_ID}'\n"
        'elif [[ "$1" == "inspect" && "$target" == "vision-webtop" ]]; then\n'
        f"  printf '%s\\n' '{WEBTOP_ID}'\n"
        f'elif [[ "$1" == "inspect" && "$target" == "{BROWSER_ID}" ]]; then\n'
        f"  printf '%s\\n' '{observed_browser_identity}'\n"
        "else\n"
        "  exit 1\n"
        "fi\n"
    )
    docker.chmod(0o755)
    for command in ("flock", "timeout"):
        executable = tmp_path / command
        executable.write_text("#!/usr/bin/env bash\nexit 0\n")
        executable.chmod(0o755)
    return {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "FB_AGENT_ROOT": str(tmp_path / "root"),
    }


def _browser_identity(network_mode: str) -> str:
    return (
        "true|healthy|fb_agent_desktop|browser-agent|true|"
        f"{CLUSTER_ID}|vision|{RELEASE_ID}|{BROWSER_IMAGE}|{network_mode}"
    )


def _maintenance_quiescence_environment(
    tmp_path: Path,
    observed_state: str,
) -> tuple[dict[str, str], Path]:
    sql_capture = tmp_path / "quiescence.sql"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "ps" ]]; then\n'
        "  printf '%s\\n' 'postgres-review fb_agent_infra'\n"
        'elif [[ "$1" == "exec" ]]; then\n'
        '  tee "$SQL_CAPTURE" >/dev/null\n'
        '  if [[ "${DOCKER_EXEC_FAIL:-0}" == "1" ]]; then exit 1; fi\n'
        "  printf '%s\\n' \"$QUIESCENCE_STATE\"\n"
        "else\n"
        "  exit 1\n"
        "fi\n"
    )
    docker.chmod(0o755)
    timeout = tmp_path / "timeout"
    timeout.write_text(
        "#!/usr/bin/env bash\n"
        'while [[ "${1:-}" == --* ]]; do shift; done\n'
        'if [[ "${1:-}" =~ ^[0-9]+$ ]]; then shift; fi\n'
        'exec "$@"\n'
    )
    timeout.chmod(0o755)
    return (
        {
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "QUIESCENCE_STATE": observed_state,
            "SQL_CAPTURE": str(sql_capture),
        },
        sql_capture,
    )


def _maintenance_enter_environment(
    tmp_path: Path,
    sequence: str,
) -> tuple[dict[str, str], Path]:
    sql_capture = tmp_path / "maintenance-enter.sql"
    sequence_index = tmp_path / "maintenance-sequence-index"
    sequence_index.write_text("0\n")
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "ps" ]]; then\n'
        "  printf '%s\\n' 'postgres-review fb_agent_infra'\n"
        'elif [[ "$1" == "exec" ]]; then\n'
        '  sql="$(cat)"\n'
        '  printf \'%s\\n\' "$sql" >>"$SQL_CAPTURE"\n'
        "  if [[ \"$sql\" == *'INSERT INTO system_config'* ]]; then\n"
        "    printf '%s\\n' \"${@: -2:1}\"\n"
        "  elif [[ \"$sql\" == *'UPDATE system_config'* ]]; then\n"
        "    printf '%s\\n' \"${@: -2:1}\"\n"
        "  elif [[ \"$sql\" == *'SELECT EXISTS'* ]]; then\n"
        "    printf '%s\\n' 't'\n"
        "  elif [[ \"$sql\" == *'FROM task_queue'* ]]; then\n"
        "    IFS=',' read -r -a states <<<\"$QUIESCENCE_SEQUENCE\"\n"
        '    index="$(<"$SEQUENCE_INDEX")"\n'
        "    last=$(( ${#states[@]} - 1 ))\n"
        "    (( index <= last )) || index=$last\n"
        "    printf '%s\\n' \"${states[$index]}\"\n"
        '    printf \'%s\\n\' "$((index + 1))" >"$SEQUENCE_INDEX"\n'
        "  elif [[ \"$sql\" == *'DELETE FROM system_config'* ]]; then\n"
        "    printf '%s\\n' 'browser_maintenance'\n"
        "  else\n"
        "    exit 1\n"
        "  fi\n"
        "else\n"
        "  exit 1\n"
        "fi\n"
    )
    docker.chmod(0o755)
    timeout = tmp_path / "timeout"
    timeout.write_text(
        "#!/usr/bin/env bash\n"
        'while [[ "${1:-}" == --* ]]; do shift; done\n'
        'if [[ "${1:-}" =~ ^[0-9]+$ ]]; then shift; fi\n'
        'exec "$@"\n'
    )
    timeout.chmod(0o755)
    return (
        {
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "QUIESCENCE_SEQUENCE": sequence,
            "SEQUENCE_INDEX": str(sequence_index),
            "SQL_CAPTURE": str(sql_capture),
        },
        sql_capture,
    )


def _browser_recreate_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    _desktop_state(tmp_path)
    runtime_state = tmp_path / "browser-runtime-state"
    runtime_state.write_text("stale\n")
    docker_log = tmp_path / "docker.log"
    webtop_identity = _vision_identity()
    current_browser = _browser_identity(f"container:{WEBTOP_ID}")
    stale_browser = _browser_identity("container:" + "9" * 64)
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'target="${!#}"\n'
        'if [[ "$1" == "ps" && "$*" == *\'com.docker.compose.service=postgres\'* ]]; then\n'
        "  printf '%s\\n' 'postgres-review fb_agent_infra'\n"
        'elif [[ "$1" == "exec" && "$2" == "postgres-review" ]]; then\n'
        '  sql="$(tee /dev/null)"\n'
        "  if [[ \"$sql\" == *'UPDATE system_config'* ]]; then\n"
        "    printf '%s\\n' \"$FB_AGENT_BROWSER_MAINTENANCE_OWNER\"\n"
        "  elif [[ \"$sql\" == *'SELECT EXISTS'* ]]; then\n"
        "    printf '%s\\n' 't'\n"
        "  elif [[ \"$sql\" == *'FROM task_queue'* ]]; then\n"
        "    printf '%s\\n' '0:0'\n"
        "  else\n"
        "    exit 1\n"
        "  fi\n"
        'elif [[ "$1" == "inspect" && "$target" == "vision-webtop" && "$*" == *\'{{.Id}}\'* ]]; then\n'
        f"  printf '%s\\n' '{WEBTOP_ID}'\n"
        'elif [[ "$1" == "inspect" && "$target" == "vision-webtop" ]]; then\n'
        f"  printf '%s\\n' '{webtop_identity}'\n"
        f'elif [[ "$1" == "inspect" && "$target" == "{BROWSER_ID}" ]]; then\n'
        '  if [[ "$(<"$FAKE_RUNTIME_STATE")" == "current" ]]; then\n'
        f"    printf '%s\\n' '{current_browser}'\n"
        "  else\n"
        f"    printf '%s\\n' '{stale_browser}'\n"
        "  fi\n"
        'elif [[ "$1" == "compose" && "$*" == *\' config --images\'* ]]; then\n'
        f"  printf '%s\\n' '{BROWSER_IMAGE}'\n"
        'elif [[ "$1" == "compose" && "$*" == *\' config --quiet\'* ]]; then\n'
        "  exit 0\n"
        'elif [[ "$1" == "compose" && "$*" == *\' ps -q browser-agent\'* ]]; then\n'
        f"  printf '%s\\n' '{BROWSER_ID}'\n"
        'elif [[ "$1" == "compose" && "$*" == *\' pull browser-agent\'* ]]; then\n'
        '  printf \'%s\\n\' "$*" >>"$FAKE_DOCKER_LOG"\n'
        'elif [[ "$1" == "compose" && "$*" == *\' up -d\'* ]]; then\n'
        '  printf \'%s\\n\' "$*" >>"$FAKE_DOCKER_LOG"\n'
        "  printf '%s\\n' 'current' >\"$FAKE_RUNTIME_STATE\"\n"
        "else\n"
        "  printf 'unexpected docker invocation: %s\\n' \"$*\" >&2\n"
        "  exit 1\n"
        "fi\n"
    )
    docker.chmod(0o755)
    timeout = tmp_path / "timeout"
    timeout.write_text(
        "#!/usr/bin/env bash\n"
        'while [[ "${1:-}" == --* ]]; do shift; done\n'
        'if [[ "${1:-}" =~ ^[0-9]+$ ]]; then shift; fi\n'
        'exec "$@"\n'
    )
    timeout.chmod(0o755)
    flock = tmp_path / "flock"
    flock.write_text("#!/usr/bin/env bash\nexit 0\n")
    flock.chmod(0o755)
    return (
        {
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "FB_AGENT_ROOT": str(tmp_path / "root"),
            "FB_AGENT_DESKTOP_LOCK_HELD": "1",
            "FB_AGENT_BROWSER_MAINTENANCE_OWNER": "a" * 32,
            "FAKE_RUNTIME_STATE": str(runtime_state),
            "FAKE_DOCKER_LOG": str(docker_log),
        },
        docker_log,
    )


def test_active_deploy_is_not_cancelled_by_new_push() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    assert "cancel-in-progress: false" in workflow


def test_systemd_keeps_app_and_desktop_lifecycles_independent() -> None:
    legacy = "/opt/fb-agent/shared/active-desktop-state/release/scripts/"
    verifier_root = "/usr/local/libexec/fb-agent-release-verifier/current/verified-release-exec.py"
    desktop_verifier = f"{verifier_root} --state desktop"
    app_verifier = f"{verifier_root} --state app"
    app_unit = (ROOT / "deploy/systemd/fb-agent.service").read_text()
    desktop_unit = (ROOT / "deploy/systemd/fb-agent-desktop-agent.service").read_text()
    healer_unit = (ROOT / "deploy/systemd/fb-agent-desktop-heal.service").read_text()

    assert legacy not in app_unit
    assert legacy not in desktop_unit
    assert legacy not in healer_unit
    assert desktop_verifier not in app_unit
    assert f"{app_verifier} --entrypoint scripts/platform-compose.sh -- up" in app_unit
    assert (
        f"{desktop_verifier} --entrypoint scripts/wait-for-vision-container.sh --" in desktop_unit
    )
    assert (
        f"{desktop_verifier} --entrypoint scripts/platform-desktop-compose.sh -- up" in desktop_unit
    )
    assert (
        f"{desktop_verifier} --entrypoint scripts/platform-desktop-compose.sh -- restart"
        in desktop_unit
    )
    assert (
        f"{desktop_verifier} --entrypoint scripts/platform-desktop-compose.sh -- stop"
        in desktop_unit
    )
    assert f"{desktop_verifier} --entrypoint scripts/platform-desktop-heal.sh --" in healer_unit
    assert "TimeoutStartSec=15min" in desktop_unit
    assert "TimeoutStopSec=5min" in desktop_unit
    assert "TimeoutStartSec=15min" in healer_unit
    assert desktop_unit.index("ExecStartPre=") < desktop_unit.index("ExecStart=")


def test_vision_wait_script_accepts_exact_running_container(tmp_path) -> None:
    env = _vision_wait_environment(tmp_path, _vision_identity())

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "wait-for-vision-container.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert "Exact committed Vision desktop is healthy" in result.stdout


def test_vision_wait_script_fails_closed_at_timeout(tmp_path) -> None:
    env = _vision_wait_environment(
        tmp_path,
        _vision_identity(running="false", health="starting"),
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "wait-for-vision-container.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "did not become healthy" in result.stderr


def test_vision_wait_script_rejects_running_foreign_cluster(tmp_path) -> None:
    env = _vision_wait_environment(
        tmp_path,
        _vision_identity(cluster_id="e" * 32),
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "wait-for-vision-container.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "did not become healthy" in result.stderr


def test_vision_wait_script_rejects_wrong_image(tmp_path) -> None:
    env = _vision_wait_environment(
        tmp_path,
        _vision_identity(
            image="ghcr.io/example/vision-webtop@sha256:" + "f" * 64,
        ),
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "wait-for-vision-container.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "Vision desktop did not become healthy" in result.stderr


def test_vision_wait_script_rejects_wrong_release_label(tmp_path) -> None:
    env = _vision_wait_environment(
        tmp_path,
        _vision_identity(release_id="stale-release"),
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "wait-for-vision-container.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "Vision desktop did not become healthy" in result.stderr


def test_installer_identity_accepts_exact_desktop() -> None:
    result = _run_installer_identity_check()

    assert result.returncode == 0, result.stderr


def test_installer_identity_rejects_foreign_desktop() -> None:
    result = _run_installer_identity_check(release_id="stale-release")

    assert result.returncode != 0


def test_desktop_compose_verify_accepts_current_webtop_binding(tmp_path) -> None:
    env = _browser_verify_environment(
        tmp_path,
        _browser_identity(f"container:{WEBTOP_ID}"),
    )

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "platform-desktop-compose.sh"),
            "verify",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr


def test_verified_desktop_consumers_accept_pinned_canonical_state_directory(
    tmp_path,
) -> None:
    browser_root = tmp_path / "browser"
    vision_root = tmp_path / "vision"
    browser_root.mkdir()
    vision_root.mkdir()
    browser_env = _browser_verify_environment(
        browser_root,
        _browser_identity(f"container:{WEBTOP_ID}"),
    )
    pinned_state = browser_root / "root" / "shared" / "desktop-states" / "committed"
    browser_env.update(
        {
            "FB_AGENT_VERIFIED_RELEASE_SCHEMA": ("fb-agent-verified-release-exec/v1"),
            "FB_AGENT_ACTIVE_STATE_DIR": str(pinned_state),
        }
    )
    browser = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "platform-desktop-compose.sh"),
            "verify",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=browser_env,
    )
    assert browser.returncode == 0, browser.stderr

    vision_env = _vision_wait_environment(
        vision_root,
        _vision_identity(),
    )
    vision_pinned_state = vision_root / "root" / "shared" / "desktop-states" / "committed"
    vision_env.update(
        {
            "FB_AGENT_VERIFIED_RELEASE_SCHEMA": ("fb-agent-verified-release-exec/v1"),
            "FB_AGENT_ACTIVE_STATE_DIR": str(vision_pinned_state),
        }
    )
    vision = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "wait-for-vision-container.sh"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=vision_env,
    )
    assert vision.returncode == 0, vision.stderr


def test_desktop_compose_verify_rejects_orphaned_webtop_binding(tmp_path) -> None:
    env = _browser_verify_environment(
        tmp_path,
        _browser_identity("container:" + "9" * 64),
    )

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "platform-desktop-compose.sh"),
            "verify",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "Vision namespace binding is not exact" in result.stderr


def test_desktop_compose_verify_rejects_unhealthy_browser(tmp_path) -> None:
    identity = _browser_identity(f"container:{WEBTOP_ID}").replace(
        "true|healthy|",
        "true|unhealthy|",
        1,
    )
    env = _browser_verify_environment(tmp_path, identity)

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "platform-desktop-compose.sh"),
            "verify",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "Vision namespace binding is not exact" in result.stderr


def test_desktop_compose_up_recreates_orphaned_browser_namespace(tmp_path) -> None:
    env, docker_log = _browser_recreate_environment(tmp_path)

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "platform-desktop-compose.sh"),
            "up",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocations = docker_log.read_text()
    assert "pull browser-agent" in invocations
    assert "up -d --force-recreate --wait --wait-timeout 180 browser-agent" in invocations


def test_desktop_compose_up_exact_runtime_skips_registry_and_mutation(tmp_path) -> None:
    env, docker_log = _browser_recreate_environment(tmp_path)
    Path(env["FAKE_RUNTIME_STATE"]).write_text("current\n")

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "platform-desktop-compose.sh"),
            "up",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert not docker_log.exists()


def test_maintenance_quiescence_accepts_no_active_browser_work(tmp_path) -> None:
    env, sql_capture = _maintenance_quiescence_environment(tmp_path, "0:0")

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source {ROOT / 'scripts/browser-maintenance-lease.sh'}; "
                "browser_maintenance_assert_quiescent"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    sql = sql_capture.read_text()
    assert "browser_operation_leases" in sql
    assert "lease_expires_at > clock_timestamp()" in sql
    assert "is_scanning_enabled" not in sql


def test_maintenance_quiescence_rejects_active_direct_browser_lease(tmp_path) -> None:
    env, _ = _maintenance_quiescence_environment(tmp_path, "0:1")

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source {ROOT / 'scripts/browser-maintenance-lease.sh'}; "
                "browser_maintenance_assert_quiescent"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "zero active browser operation leases" in result.stderr


def test_maintenance_enter_waits_for_existing_browser_work_to_drain(tmp_path) -> None:
    env, sql_capture = _maintenance_enter_environment(tmp_path, "1:1,0:0")

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                "sleep() { :; }; "
                f"source {ROOT / 'scripts/browser-maintenance-lease.sh'}; "
                "browser_maintenance_start_renewal() { return 0; }; "
                "browser_maintenance_enter; "
                '[[ "$BROWSER_MAINTENANCE_HELD" == true ]]; '
                "browser_maintenance_leave"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    sql = sql_capture.read_text()
    assert sql.count("FROM task_queue") == 2
    assert "DELETE FROM system_config" in sql


def test_maintenance_enter_fails_bounded_and_releases_when_work_never_drains(
    tmp_path,
) -> None:
    env, sql_capture = _maintenance_enter_environment(tmp_path, "1:1")

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                "sleep() { SECONDS=$((SECONDS + 60)); }; "
                f"source {ROOT / 'scripts/browser-maintenance-lease.sh'}; "
                "browser_maintenance_start_renewal() { return 0; }; "
                "if browser_maintenance_enter; then exit 99; fi; "
                '[[ "$BROWSER_MAINTENANCE_HELD" == false ]]'
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "did not drain within 55s" in result.stderr
    assert "DELETE FROM system_config" in sql_capture.read_text()


def test_maintenance_adopt_never_marks_an_expired_owner_as_held(tmp_path) -> None:
    env, _ = _maintenance_quiescence_environment(tmp_path, "f")

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source {ROOT / 'scripts/browser-maintenance-lease.sh'}; "
                f"browser_maintenance_adopt {'a' * 32}"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0


def test_maintenance_release_reports_database_failure(tmp_path) -> None:
    env, _ = _maintenance_quiescence_environment(tmp_path, "")
    env["DOCKER_EXEC_FAIL"] = "1"

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source {ROOT / 'scripts/browser-maintenance-lease.sh'}; "
                f"BROWSER_MAINTENANCE_OWNER={'a' * 32}; "
                "BROWSER_MAINTENANCE_HELD=true; "
                "browser_maintenance_release"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0


def test_maintenance_release_requires_exact_deleted_owner_row(tmp_path) -> None:
    env, _ = _maintenance_quiescence_environment(tmp_path, "")

    missing = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source {ROOT / 'scripts/browser-maintenance-lease.sh'}; "
                f"BROWSER_MAINTENANCE_OWNER={'a' * 32}; "
                "BROWSER_MAINTENANCE_HELD=true; "
                "browser_maintenance_release"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert missing.returncode != 0

    env["QUIESCENCE_STATE"] = "browser_maintenance"
    deleted = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source {ROOT / 'scripts/browser-maintenance-lease.sh'}; "
                f"BROWSER_MAINTENANCE_OWNER={'a' * 32}; "
                "BROWSER_MAINTENANCE_HELD=true; "
                "browser_maintenance_release; "
                '[[ "$BROWSER_MAINTENANCE_HELD" == false ]]'
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert deleted.returncode == 0, deleted.stderr
