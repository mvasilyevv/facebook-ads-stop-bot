from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_backup_and_pitr_gate_orders_fresh_and_existing_migrations_safely() -> None:
    deploy = _source("scripts/bluegreen-deploy.sh")
    gate = _source("scripts/release-backup-gate.sh")

    assert 'if [[ -z "$PREVIOUS_COLOR" ]]; then' in deploy
    fresh_branch, existing_branch = deploy.split(
        "# Existing releases must prove a restorable pre-change backup",
        maxsplit=1,
    )
    fresh_branch = fresh_branch.rsplit(
        'if [[ -z "$PREVIOUS_COLOR" ]]; then',
        maxsplit=1,
    )[1]
    assert fresh_branch.index("--profile migration run --rm migrator") < fresh_branch.index(
        "release-backup-gate.sh"
    )
    assert (
        fresh_branch.index("--profile migration run --rm migrator")
        < fresh_branch.index("adoption_importer")
        < fresh_branch.index("release-backup-gate.sh")
    )
    assert 'ADOPTION_BUNDLE_FILE="$STATE_DIR/adoption-bundle-v1.json"' in deploy
    assert "first release requires the reviewed adoption bundle" in deploy
    assert existing_branch.index("release-backup-gate.sh") < existing_branch.index(
        "--profile migration run --rm migrator"
    )
    assert "adoption-$RELEASE_ID" in fresh_branch
    assert "pre-migration" in existing_branch
    assert "pgbackrest-admin.sh" in gate
    assert "pgbackrest-restore-drill.sh" in gate
    assert "--prove-post-backup-wal" in gate
    assert "--require-pitr-marker" in gate
    assert "--expected-release-id" in gate
    assert "--max-age-seconds" in gate


def test_platform_bootstrap_uses_the_shared_complete_database_guard() -> None:
    bootstrap = _source("scripts/platform-bootstrap.sh")

    assert "migrations/baseline_contract.py" in bootstrap
    assert "--render-platform-psql-guard" in bootstrap
    assert "DO $fresh_target_guard$" not in bootstrap
    assert "'pg_catalog.pg_type'::pg_catalog.regclass" not in bootstrap
    assert "'pg_catalog.pg_collation'::pg_catalog.regclass" not in bootstrap


def test_release_id_reuse_is_read_only_and_content_identical() -> None:
    deploy = _source("scripts/deploy-platform-server.sh")
    state = _source("scripts/release-state.py")

    existing = deploy.split("existing)", maxsplit=1)[1].split("new)", maxsplit=1)[0]
    new = deploy.split("new)", maxsplit=1)[1].split(
        '*) die "unexpected remote release status',
        maxsplit=1,
    )[0]
    assert "\n    rsync " not in existing
    assert "different source content" in existing
    assert "different image manifest" in existing
    assert "manifest-verify" in existing
    assert "--require-read-only" in existing
    assert "sha256sum --check --strict .fb-agent-release" in existing
    assert "readlink '$remote_release/.env'" in existing
    assert ".incoming-${release_id}-" in new
    assert 'remote_lock_publish "$REMOTE_STAGING" "$remote_release"' in new
    assert ".fb-agent-effective-config.sha256" in new
    assert "find '$REMOTE_STAGING' -xdev -type f -exec chmod a-w" in new
    assert "--require-read-only" in new
    assert "manifest-write" in deploy
    assert 'REMOTE_LOCK="$ROOT_DIR/shared/.platform-deploy.lock"' in deploy
    assert ".release-upload-${release_id}.lock" not in deploy
    assert "release tree differs from its immutable source manifest" in state
    assert '(b"source_manifest", source_manifest)' in state
    assert "effective_config_fingerprint" in state
    runtime = _source("scripts/platform-compose.sh")
    assert "release_tree_is_immutable" in runtime
    assert "manifest-verify" in runtime


def test_release_adoption_fingerprint_lock_and_origin_are_fail_closed() -> None:
    upload = _source("scripts/deploy-platform-server.sh")
    parent = _source("scripts/server-platform-release.sh")
    child = _source("scripts/bluegreen-deploy.sh")
    reconciler = _source("scripts/reconcile-platform-release.sh")

    assert 'REMOTE_LOCK="$ROOT_DIR/shared/.platform-deploy.lock"' in upload
    assert upload.index("remote_lock_action acquire") < upload.index(
        "provision-bootstrap-secrets.py"
    )
    assert upload.index("provision-bootstrap-secrets.py") < upload.index(
        'remote_lock_publish "$REMOTE_STAGING" "$remote_release"'
    )
    assert upload.index('remote_lock_publish "$REMOTE_STAGING" "$remote_release"') < upload.index(
        'ssh "$TARGET" "$remote_command"'
    )

    fingerprint = parent.index("desired effective production config fingerprint")
    desired_render = parent.index('expected_app_env="$TEMP_DIR/production.env"')
    pending_reconciliation = parent.index('if [[ -f "$STATE_DIR/release-transaction.json" ]]')
    reconciler_install = parent.index('"$SCRIPT_DIR/install-release-reconciler.sh"')
    prepare = parent.index('"$SCRIPT_DIR/release-state.py" prepare')
    begin = parent.index('"$SCRIPT_DIR/release-state.py" begin')
    assert fingerprint < prepare < begin
    assert desired_render < pending_reconciliation < reconciler_install
    assert 'cmp -s -- "$expected_app_env" "$APP_ENV"' in parent
    assert ".fb-agent-effective-config.sha256" in parent
    for source in (parent, child, reconciler, _source("scripts/release-state.py")):
        assert "sync-desired-env" not in source

    assert 'readonly CANONICAL_PUBLIC_URL="https://app.adpulse.su"' in child
    assert "only the canonical public URL" in child
    assert child.index("only the canonical public URL") < child.index('for file in "$RELEASE_ENV"')
    assert 'release-state.py" complete' not in child

    for stage in (
        "alloy_adopted",
        "timers_adopted",
        "systemd_adopted",
        "desktop_adopted",
    ):
        assert f"--stage {stage}" in parent
        assert stage in reconciler
    assert "resume_post_commit_adoption" in reconciler


def test_shared_network_compose_files_emit_strict_inventory_labels() -> None:
    app = _source("deploy/compose/docker-compose.app.yml")
    infra = _source("deploy/compose/docker-compose.infra.yml")
    telemetry = _source("deploy/monitoring/docker-compose.agent.yml")

    for source, purpose in (
        (app, "app"),
        (infra, "infra"),
        (telemetry, "telemetry"),
    ):
        assert 'com.fb-agent.managed: "true"' in source
        assert "com.fb-agent.cluster-id:" in source
        assert f"com.fb-agent.purpose: {purpose}" in source
    assert "com.fb-agent.color:" in app
    assert "com.fb-agent.release:" in app
    assert "com.fb-agent.color:" not in infra
    assert "com.fb-agent.release:" not in infra
    assert "com.fb-agent.release:" in telemetry
    assert "com.fb-agent.color:" not in telemetry


def test_mismatched_existing_release_fails_before_rsync(tmp_path: Path) -> None:
    release_env = tmp_path / "release-images.env"
    release_env.write_text(
        "\n".join(
            (
                "RELEASE_ID=repeat-release",
                f"DESKTOP_WEBTOP_IMAGE=example/webtop@sha256:{'1' * 64}",
                "",
            )
        ),
        encoding="utf-8",
    )
    calls = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(
        """#!/usr/bin/env bash
set -eu
shift
printf 'SSH %s\\n' "$*" >>"$CALL_LOG"
case "$*" in
  *"if test -e "* ) printf existing ;;
  *".fb-agent-source-manifest.json"*"sha256sum"* ) printf '%064d\\n' 0 ;;
  *"release-images.env"*"sha256sum"* ) printf '%064d\\n' 0 ;;
esac
""",
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    rsync = bin_dir / "rsync"
    rsync.write_text(
        """#!/usr/bin/env bash
set -eu
printf 'RSYNC %s\\n' "$*" >>"$CALL_LOG"
""",
        encoding="utf-8",
    )
    rsync.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/deploy-platform-server.sh"),
            "--host",
            "deploy@example.test",
            "--release-env",
            str(release_env),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALL_LOG": str(calls),
        },
    )

    assert result.returncode != 0
    assert "already exists with different source content" in result.stderr
    assert "RSYNC " not in calls.read_text(encoding="utf-8")


def test_supported_release_owns_backup_timer_adoption_and_alloy_readiness() -> None:
    release = _source("scripts/server-platform-release.sh")
    backup = _source("scripts/install-platform-units.sh")
    alloy_install = _source("scripts/install-alloy-agent-unit.sh")
    alloy_runtime = _source("scripts/platform-alloy-agent.sh")

    assert not (ROOT / "scripts/initialize-backup-adoption.sh").exists()
    assert "adoption-$release_id" in release
    assert "--expected-release-id" in release
    assert "--verify-only" in release
    assert release.index(
        "install-platform-units.sh", release.index("optional Redis")
    ) < release.index('"$SCRIPT_DIR/bluegreen-deploy.sh"')
    assert release.count("install-alloy-agent-unit.sh") == 2
    assert "--validate-only" in release
    assert 'active_release_id="$(state --source active --field release_id)"' in release
    assert "backup-evidence/timer-recovery" in release
    assert '--release-env "$active_release_env"' in release
    assert '--accepted-dir "$recovery_evidence"' in release
    app_release = release.index('"$SCRIPT_DIR/bluegreen-deploy.sh"')
    desktop_install = release.index(
        '"$SCRIPT_DIR/platform-desktop-release.sh"',
        app_release,
    )
    alloy_promote = release.index(
        '"$SCRIPT_DIR/platform-alloy-agent.sh" "${alloy_promote_args[@]}"',
        desktop_install,
    )
    timer_install = release.index(
        "--entrypoint scripts/install-platform-units.sh",
        alloy_promote,
    )
    assert app_release < desktop_install < alloy_promote < timer_install
    assert release.count('--profile-seed-dir "$DESKTOP_PROFILE_SEED_DIR"') == 4
    desktop_preflight = release.index("--preflight-only")
    assert desktop_preflight < app_release < desktop_install
    assert '--deadline-epoch "$CUTOVER_DEADLINE_EPOCH"' in release[desktop_install:]
    for timer in (
        "fb-agent-pgbackrest-full.timer",
        "fb-agent-pgbackrest-diff.timer",
        "fb-agent-restore-drill.timer",
    ):
        assert timer in backup
    assert "systemctl is-enabled --quiet" in backup
    assert "systemctl is-active --quiet" in backup
    assert 'systemctl start "$UNIT_NAME"' in alloy_install
    assert 'platform-alloy-agent.sh" ready' in alloy_install
    assert "canonical_ready" in alloy_runtime
    for endpoint in ("PROMETHEUS_READY_URL", "LOKI_READY_URL", "TEMPO_READY_URL"):
        assert endpoint in alloy_runtime
    assert '[[ "$status" =~ ^2[0-9][0-9]$ ]]' in alloy_runtime
    assert "fb_agent_telemetry_candidate" in alloy_runtime
    assert "candidate_cleanup" in alloy_runtime
    assert "MONITORING_TRANSPORT" in alloy_install
    assert "same_host" in alloy_install
    assert "host.docker.internal:9090" in alloy_install
    assert "http://172.17.0.1:9090/-/ready" in alloy_runtime
    alloy_gate = release.index('"$SCRIPT_DIR/platform-alloy-agent.sh" candidate-up')
    app_cutover = release.index('"$SCRIPT_DIR/bluegreen-deploy.sh"')
    assert alloy_gate < app_cutover
    assert 'FB_AGENT_RELEASE_DIR="$RELEASE_DIR"' in release[:app_cutover]
    assert app_cutover < alloy_promote
    assert "--previous-release-dir" in release[app_cutover:alloy_promote]


def test_desktop_cutover_requires_explicit_seed_cdp_and_authenticated_desktop() -> None:
    release = _source("scripts/server-platform-release.sh")
    desktop = _source("scripts/platform-desktop-release.sh")
    healer = _source("scripts/platform-desktop-heal.sh")
    desktop_runtime = _source("scripts/platform-desktop-compose.sh")
    vision = _source("scripts/install-vision-webtop.sh")
    transaction = _source("scripts/platform-desktop-transaction.sh")
    maintenance = _source("scripts/browser-maintenance-lease.sh")

    assert "--validate-profile-seed-only" in release
    assert "--profile-seed-dir" in release
    assert "desktop-profile-seed" in release
    assert "fresh installation refused incumbent desktop state" in release
    assert "fresh installation refused an incumbent Vision desktop container" in release
    for project in ("fb_agent", "fb_agent_blue", "fb_agent_green", "fb_agent_desktop"):
        assert project in release
    assert "/api/settings/vision" in desktop
    assert "/api/vision/ensure-cdp" in desktop
    assert "/desktop-readyz" in desktop
    assert "dist/meta-api/health-probe-cli.js" in desktop
    assert "desktop-vision-contract.py" in healer
    for check in ("configured", "auth_challenge", "authenticated"):
        assert check in desktop
    transaction_prepare = desktop.rindex('"$SCRIPT_DIR/platform-desktop-transaction.sh" prepare')
    main_browser_remove = desktop.index(
        'remove_browser_container "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV"',
        transaction_prepare,
    )
    deferred_vision = desktop.index("--defer-commit", main_browser_remove)
    candidate_ready = desktop.index(
        "wait_for_candidate_desktop_readiness",
        deferred_vision,
    )
    candidate_identity = desktop.index(
        "browser_identity_is_exact",
        candidate_ready,
    )
    state_commit = desktop.index('"$ACTIVE_DESKTOP_STATE"', transaction_prepare)
    unit_install = desktop.index('install_desktop_units "$PROJECT_DIR"', transaction_prepare)
    transaction_reconcile = desktop.index(
        '"$SCRIPT_DIR/platform-desktop-transaction.sh" reconcile',
        state_commit,
    )
    committed_ready = desktop.index(
        "wait_for_committed_desktop_readiness",
        transaction_reconcile,
    )
    final_identity = desktop.index("browser_identity_is_exact", state_commit)
    vision_commit = desktop.index("--reconcile-pending-update", state_commit)
    transaction_complete = desktop.index(
        '"$SCRIPT_DIR/platform-desktop-transaction.sh" complete --expect candidate',
        vision_commit,
    )
    release_disarm = desktop.index("ROLLBACK_ARMED=false", vision_commit)
    assert (
        transaction_prepare
        < main_browser_remove
        < deferred_vision
        < candidate_ready
        < candidate_identity
        < unit_install
        < state_commit
    )
    assert state_commit < final_identity < transaction_reconcile < committed_ready
    assert deferred_vision < state_commit < vision_commit < transaction_complete < release_disarm
    assert "desktop-transaction.env" in transaction
    assert 'sync -f "$STATE_DIR"' in transaction
    readiness_commit = transaction.index(
        'atomic_relative_symlink "states/${target_state}.env" "$ACTIVE_READINESS"'
    )
    caddy_commit = transaction.index("--caddy-only --sync-scope desktop", readiness_commit)
    unit_commit = transaction.index("install_units_for_state", caddy_commit)
    assert readiness_commit < caddy_commit < unit_commit
    assert "BROWSER_MAINTENANCE_RENEW_SECONDS" in maintenance
    assert "browser_maintenance_renew" in maintenance
    assert "browser_maintenance_checkpoint" in maintenance
    assert '"/proc/$BASHPID/status"' in maintenance
    assert '"$observed_parent" != "$main_pid"' in maintenance
    assert "pg_advisory_xact_lock" in maintenance
    lease_acquire = desktop.index("acquire_browser_maintenance")
    contract_capture = desktop.index("load_configured_vision_contract", lease_acquire)
    assert lease_acquire < contract_capture < transaction_prepare
    assert "configuration_revision" in desktop
    assert "X-FB-Agent-Browser-Maintenance-Owner" in desktop
    assert desktop.count("configured_vision_contract_is_unchanged") >= 4
    assert "urlsafe_b64encode" in desktop
    main_browser_remove = desktop.rindex(
        "remove_browser_container",
        0,
        deferred_vision,
    )
    final_browser_remove = desktop.index(
        "remove_browser_container",
        state_commit,
    )
    final_browser_recreate = desktop.index(
        "--force-recreate",
        vision_commit,
    )
    assert main_browser_remove < deferred_vision
    assert state_commit < final_browser_remove < vision_commit < final_browser_recreate
    assert ".fb-agent-source-manifest.json" in desktop
    assert ".fb-agent-vision-profile-v1" in vision
    assert ".fb-agent-seed-bootstrap-v1" in vision
    assert "desktop profile seed changed while its snapshot was created" in vision
    assert "unmanaged desktop config cannot resume clean bootstrap" in vision
    assert ".vision-update.env" in vision
    assert "snapshot_ready" in vision
    assert "capture_runtime_contract" in vision
    assert "rollback 1" in vision
    assert 'local exit_code="${1:-$?}"' in vision
    assert 'VISION_COMPOSE_ENV_FILE="$PREVIOUS_VISION_ENV"' in desktop
    assert 'VISION_ROLLBACK_ENV_FILE="$PREVIOUS_VISION_ENV"' in desktop
    assert "desktop_rollback_nonconvergent" in desktop
    assert "desktop_rollback_fence_lost" in desktop
    assert "vision_inner_rollback_nonconvergent" in vision
    assert "browser_maintenance" in desktop
    assert "desktop-release.lock" in desktop
    assert 'if [[ "$ROLLBACK_ARMED" == true ]]; then\n    rollback 1' in desktop
    assert 'local -r exit_code="$1"' in desktop
    assert desktop.index("ROLLBACK_ARMED=true\ntrap 'rollback $?' ERR") < transaction_prepare
    assert desktop.rindex("ROLLBACK_ARMED=false\ntrap - ERR") > transaction_complete
    healer_lock = healer.index("flock -n 8")
    assert "healer deferred to the next timer tick" in healer
    fast_status = healer.index("platform-desktop-transaction.sh", healer_lock)
    fast_ready = healer.index("desktop_runtime_is_exact_and_ready", fast_status)
    healer_fence = healer.index("browser_maintenance_enter", fast_ready)
    desktop_reconcile = healer.index("platform-desktop-transaction.sh", healer_fence)
    healer_browser_remove = healer.index("platform_desktop_compose remove", desktop_reconcile)
    healer_reconcile = healer.index("--reconcile-pending-update", healer_browser_remove)
    healer_browser_start = healer.index("platform_desktop_compose up", healer_reconcile)
    assert (
        healer_lock
        < fast_status
        < fast_ready
        < healer_fence
        < desktop_reconcile
        < healer_browser_remove
        < healer_reconcile
        < healer_browser_start
    )
    assert healer.index("record_host_metric started") > fast_ready
    assert "X-FB-Agent-Browser-Maintenance-Owner" in healer
    assert "--force-recreate" in desktop_runtime
    assert ".HostConfig.NetworkMode" in desktop_runtime
    assert ".HostConfig.IpcMode" not in vision
    assert "vision_identity_is_exact" in vision
    runtime_start = desktop_runtime.index("start_browser()")
    exact_runtime = desktop_runtime.index("if browser_identity_is_exact", runtime_start)
    registry_pull = desktop_runtime.index('"${compose[@]}" pull browser-agent', runtime_start)
    assert exact_runtime < registry_pull
    assert "Vision desktop namespace is not running" not in healer


def test_desktop_candidate_health_uses_host_literal_contract_not_candidate_verdict() -> None:
    desktop = _source("scripts/platform-desktop-release.sh")
    function_body = desktop.split("vision_control_ready() {", 1)[1].split(
        "\n}\n\nprevious_vision_control_ready()",
        1,
    )[0]

    assert "set -Eeuo pipefail" in desktop
    assert 'probe_timeout="$(timeout_cap 25)"' in function_body
    assert (
        'response="$(timeout --signal=TERM "$probe_timeout" docker exec "$container_id"'
    ) in function_body
    assert 'health-probe-cli.js --json "$EXPECTED_PROFILE_ID"' in function_body
    parser = 'python3 "$PROJECT_DIR/scripts/desktop-vision-contract.py" browser-ready'
    assert parser in function_body
    assert "printf '%s' \"$response\"" in function_body
    assert '--expected-profile-id "$EXPECTED_PROFILE_ID"' in function_body
    assert function_body.index("health-probe-cli.js") < function_body.index(parser)
    assert ">/dev/null" not in function_body
    assert "required_browser_contract_version" in desktop
    assert 'python3 "$PROJECT_DIR/scripts/desktop-vision-contract.py" required-version' in desktop
    assert (
        '"$EXPECTED_BROWSER_CONTRACT_VERSION" == "$CANDIDATE_BROWSER_CONTRACT_VERSION"' in desktop
    )
    previous_body = desktop.split("previous_vision_control_ready() {", 1)[1].split(
        "\n}\n\nconfigured_vision_contract()",
        1,
    )[0]
    assert "health-probe-cli.js --json" in previous_body
    assert (
        '"$PREVIOUS_RELEASE_DIR/scripts/desktop-vision-contract.py" browser-ready' in previous_body
    )
    assert "/api/settings/vision" not in previous_body


def test_platform_release_fences_claims_across_app_and_browser_contract_cutover() -> None:
    release = _source("scripts/server-platform-release.sh")

    candidate_up = release.index('"$SCRIPT_DIR/platform-alloy-agent.sh" candidate-up')
    maintenance_enter = release.index("browser_maintenance_enter", candidate_up)
    quiescence = release.index("browser_maintenance_checkpoint", maintenance_enter)
    app_cutover = release.index('"$SCRIPT_DIR/bluegreen-deploy.sh"', quiescence)
    post_handoff_checkpoint = release.index(
        "browser_maintenance_checkpoint",
        app_cutover,
    )
    desktop_cutover = release.index(
        '"$SCRIPT_DIR/platform-desktop-release.sh"',
        post_handoff_checkpoint,
    )
    post_desktop_checkpoint = release.index(
        "browser_maintenance_checkpoint",
        desktop_cutover,
    )
    maintenance_leave = release.index(
        "browser_maintenance_leave",
        post_desktop_checkpoint,
    )

    assert (
        candidate_up
        < maintenance_enter
        < quiescence
        < app_cutover
        < post_handoff_checkpoint
        < desktop_cutover
        < post_desktop_checkpoint
        < maintenance_leave
    )
    assert (
        '[[ -z "${FB_AGENT_BROWSER_MAINTENANCE_OWNER:-}" ]]'
        in release[candidate_up:maintenance_enter]
    )
    assert "DESKTOP_CUTOVER_STARTED=true" in release[post_handoff_checkpoint:desktop_cutover]
    assert "DESKTOP_CUTOVER_COMPLETED=true" in release[desktop_cutover:post_desktop_checkpoint]
    rollback = release[
        release.index("rollback_parent_release() {") : release.index("while (($#)); do")
    ]
    assert 'release-state.py" rollback-commit' in rollback
    assert "coordinated_app_browser_rollback:pointer_restore_failed" in rollback


def test_coordinated_release_uses_one_deadline_and_fail_closed_desktop_rollback() -> None:
    parent = _source("scripts/server-platform-release.sh")
    desktop = _source("scripts/platform-desktop-release.sh")
    reconcile = _source("scripts/reconcile-platform-release.sh")

    app_cutover = parent.index('"$SCRIPT_DIR/bluegreen-deploy.sh"')
    deadline_load = parent.index("load_cutover_deadline", app_cutover)
    desktop_cutover = parent.index(
        '"$SCRIPT_DIR/platform-desktop-release.sh"',
        deadline_load,
    )
    assert app_cutover < deadline_load < desktop_cutover
    assert '--deadline-epoch "$CUTOVER_DEADLINE_EPOCH"' in parent[desktop_cutover:]
    assert "run_supervised_child" in parent[app_cutover:desktop_cutover]
    assert "terminate_supervised_child" in parent
    assert 'kill -TERM -- "-$ACTIVE_CHILD_PGID"' in parent
    assert "trap 'handle_termination 129' HUP" in parent
    assert "desktop_release_outcome" in parent
    for outcome in (
        "candidate_final",
        "candidate_pending",
        "previous_final",
        "previous_pending",
        "absent_final",
        "absent_pending",
        "invalid",
    ):
        assert outcome in parent
    assert "desktop_candidate_pending_forward_reconciliation" in parent
    assert "PRESERVE_MAINTENANCE_LEASE=true" in parent
    assert "browser_maintenance_stop_renewal" in parent

    assert "readonly ROLLBACK_RESERVE_SECONDS=60" in desktop
    assert "FORWARD_DEADLINE_EPOCH=$((DEADLINE_EPOCH - ROLLBACK_RESERVE_SECONDS))" in desktop
    assert "--deadline-epoch) DEADLINE_EPOCH=" in desktop
    assert "desktop cutover requires --deadline-epoch" in desktop
    assert "run_before_deadline" in desktop
    assert "sleep_before_deadline 2" in desktop
    assert "wait-timeout 180" not in desktop
    assert "max-time 120" not in desktop
    assert "trap 'handle_termination 129' HUP" in desktop
    assert "desktop_rollback_deadline_exhausted" in desktop
    assert "PRESERVE_MAINTENANCE_LEASE=true" in desktop
    assert "--preflight-only" in desktop
    preflight = desktop.index('if [[ "$PREFLIGHT_ONLY" == true ]]')
    runtime_mutation = desktop.index("\nacquire_browser_maintenance\n", preflight)
    for rollback_capability in (
        "desktop_webtop_pull",
        "previous_rollback_contract_is_compatible",
        "previous_runtime_matches_active_app",
    ):
        assert desktop.index(rollback_capability, preflight) < runtime_mutation
    rollback_runtime = desktop.split("previous_runtime_direct_is_exact_and_ready() {", 1)[1].split(
        "\n}\n\nprevious_runtime_matches_active_app()", 1
    )[0]
    assert "previous_vision_control_ready" in rollback_runtime
    assert 'desktop_auth_ready "$PREVIOUS_VISION_ENV"' in rollback_runtime
    assert "committed_desktop_auth_ready" not in rollback_runtime
    assert "browser_identity_is_exact" in rollback_runtime
    active_match = desktop.split("previous_runtime_matches_active_app() {", 1)[1].split(
        "\n}\n\nconfigured_vision_contract()", 1
    )[0]
    assert "previous_runtime_direct_is_exact_and_ready" in active_match
    assert "committed_desktop_auth_ready" in active_match

    assert '--deadline-epoch "$DEADLINE_EPOCH"' in reconcile
    assert "adoption_deadline_epoch" not in reconcile


def test_parent_explicit_failures_after_begin_are_reconciled() -> None:
    release = _source("scripts/server-platform-release.sh")

    assert 'if [[ "$ROLLBACK_ARMED" == true ]]; then\n    rollback_parent_release 1' in release
    assert 'local -r exit_code="$1"' in release
    assert 'local -r rollback_was_armed="$ROLLBACK_ARMED"' in release
    assert 'if [[ "$rollback_was_armed" == true \\' in release
    armed = release.index("ROLLBACK_ARMED=true\ntrap 'rollback_parent_release $?' ERR")
    begin = release.index('"$SCRIPT_DIR/release-state.py" begin')
    cutover = release.index('"$SCRIPT_DIR/bluegreen-deploy.sh"')
    disarmed = release.index("ROLLBACK_ARMED=false\ntrap - ERR", cutover)
    assert begin < armed < cutover < disarmed


def test_first_release_installs_a_boot_safe_reconciler_before_any_mutation() -> None:
    release = _source("scripts/server-platform-release.sh")
    reconcile = _source("scripts/reconcile-platform-release.sh")

    install = release.index('"$SCRIPT_DIR/install-release-reconciler.sh"')
    prepare = release.index('"$SCRIPT_DIR/release-state.py" prepare')
    begin = release.index('"$SCRIPT_DIR/release-state.py" begin')
    caddy = release.index('"$SCRIPT_DIR/install-server-units.sh"')
    candidate = release.index('"$SCRIPT_DIR/platform-alloy-agent.sh" candidate-up')
    cutover = release.index('"$SCRIPT_DIR/bluegreen-deploy.sh"')

    assert release.count("install-release-reconciler.sh") == 1
    assert install < prepare < begin < caddy < candidate < cutover
    assert "no release transaction or committed release exists" in reconcile
    assert 'if [[ "$BOOT_MODE" == true && "$journal_present" == false \\' in reconcile


def test_remote_deploy_requires_an_explicit_host() -> None:
    deploy = _source("scripts/deploy-platform-server.sh")
    workflow = _source(".github/workflows/deploy.yml")

    assert 'TARGET=""' in deploy
    assert "62.60.150.133" not in deploy
    assert "--host is required; there is no implicit deployment target" in deploy
    deploy_step = workflow.split("- name: Деплой релиза", maxsplit=1)[1].split(
        "- name: Удалить временную GHCR-авторизацию",
        maxsplit=1,
    )[0]
    assert "DEPLOY_TARGET: ${{ secrets.DEPLOY_USER }}@${{ secrets.DEPLOY_HOST }}" in deploy_step
    assert '--host "$DEPLOY_TARGET"' in deploy_step


def test_pgbackrest_config_uses_durable_shared_path() -> None:
    compose = _source("deploy/compose/docker-compose.infra.yml")
    release = _source("scripts/server-platform-release.sh")
    runtime = _source("scripts/platform-compose.sh")

    assert "${PGBACKREST_CONFIG_FILE:?set PGBACKREST_CONFIG_FILE}" in compose
    assert "../backup/pgbackrest.conf" not in compose
    assert 'readonly PGBACKREST_CONFIG="$STATE_DIR/pgbackrest.conf"' in release
    assert "update it explicitly in shared state before deploy" in release
    assert 'export PGBACKREST_CONFIG_FILE="$PGBACKREST_CONFIG"' in runtime


def test_pgbackrest_uses_configured_database_owner() -> None:
    compose = (ROOT / "deploy/compose/docker-compose.infra.yml").read_text()
    config = (ROOT / "deploy/backup/pgbackrest.conf").read_text()

    assert "PGBACKREST_PG1_USER: ${POSTGRES_USER:?set POSTGRES_USER}" in compose
    assert "pg1-user=postgres" not in config


def test_deploy_and_every_reconciler_share_one_flock() -> None:
    parent = _source("scripts/server-platform-release.sh")
    deploy = _source("scripts/bluegreen-deploy.sh")
    reconcile = _source("scripts/reconcile-platform-release.sh")

    assert "export FB_AGENT_DEPLOY_LOCK_FD=9" in parent
    for source in (deploy, reconcile):
        assert "FB_AGENT_DEPLOY_LOCK_FD" in source
        assert 'flock -n "$FB_AGENT_DEPLOY_LOCK_FD"' in source
        assert "deploy.lock" in source


def test_candidate_openapi_must_match_the_reviewed_full_contract() -> None:
    deploy = _source("scripts/bluegreen-deploy.sh")

    for operation in (
        '("/api/operator/snapshot", "get")',
        '("/api/operator/actions", "get")',
        '("/api/operator/ads", "get")',
        '("/api/operator/ads/{ad_id}/pause", "post")',
        '("/api/operator/ads/{ad_id}/activate", "post")',
        '("/api/operator/incidents/{incident_id}", "get")',
        '("/api/operator/incidents/{incident_id}/ack", "post")',
        '("/api/analytics/performance", "get")',
        '("/api/analytics/live-budget", "get")',
        '("/api/analytics/daypart", "get")',
        '("/api/v1/integrations/telegram/webhook", "post")',
    ):
        assert operation in deploy
    assert '"$PROJECT_DIR/frontend/openapi.json"' in deploy
    assert "candidate OpenAPI differs from reviewed artifact" in deploy
    assert "hashlib.sha256" in deploy


def test_restore_evidence_is_built_from_observed_state() -> None:
    evidence = _source("scripts/backup-adoption-evidence.py")
    restore = _source("scripts/pgbackrest-restore-drill.sh")
    admin = _source("scripts/pgbackrest-admin.sh")

    assert '"pg_is_in_recovery": False' not in evidence
    assert '"production_volume_mounted": False' not in evidence
    assert '"repository": {"type": "s3"' not in evidence
    assert "docker inspect --format" in restore
    assert "current_setting" in restore
    assert '--config "$CONFIG_FILE"' in admin


def test_obsolete_vision_automation_is_absent_from_desktop_aware_runtime() -> None:
    installer = _source("scripts/install-server-units.sh")
    healer = _source("scripts/platform-desktop-heal.sh")
    unit = _source("deploy/systemd/fb-agent-desktop-heal.service")

    assert not (ROOT / "scripts/start_vision_session.py").exists()
    for obsolete_unit in (
        "xvfb.service",
        "vision-desktop.service",
        "vision.service",
        "vision-autostart.service",
        "vision-cdp-heal.timer",
        "vision-token-refresh.timer",
        "fb-browser-agent.service",
        "rustdesk.service",
    ):
        assert obsolete_unit not in installer
    assert "fb-agent-desktop-heal.timer" in installer
    assert 'DESKTOP_STATE_DIR="$FB_AGENT_ACTIVE_STATE_DIR"' in healer
    assert 'APP_STATE_DIR="$FB_AGENT_APP_STATE_DIR"' in healer
    assert 'APP_COLOR_FILE="$APP_STATE_DIR/color"' in healer
    assert "$STATE_DIR/active-app.env" not in healer
    assert "$STATE_DIR/active-color" not in healer
    assert "$STATE_DIR/active-desktop-state" not in healer
    assert "platform-desktop-compose.sh" in healer
    assert "/api/vision/ensure-cdp" in healer
    assert "platform-desktop-heal.sh" in unit


def test_desktop_units_start_before_desktop_commit_is_disarmed() -> None:
    installer = _source("scripts/install-server-units.sh")
    desktop = _source("scripts/platform-desktop-release.sh")

    start_block = installer.split("systemctl start", 1)[1].split("# The desktop units", 1)[0]
    assert "fb-agent-desktop-agent.service" not in start_block
    assert "fb-agent-desktop-heal.timer" not in start_block
    assert (
        "/usr/local/libexec/fb-agent-release-verifier/current/verified-release-exec.py"
    ) in desktop

    rollback_definition = desktop.index("rollback() {")
    exact_retry = desktop.index(
        '&& "$CANDIDATE_STATE" == "$PREVIOUS_STATE" ]]',
        rollback_definition,
    )
    exact_verification = desktop.index(
        "wait_for_committed_desktop_readiness",
        exact_retry,
    )
    exact_disarm = desktop.index("ROLLBACK_ARMED=false", exact_verification)
    exact_release = desktop.index(
        "release_browser_maintenance",
        exact_disarm,
    )
    assert rollback_definition < exact_retry < exact_verification < exact_disarm < exact_release

    transaction_complete = desktop.rindex(
        '"$SCRIPT_DIR/platform-desktop-transaction.sh" complete --expect candidate'
    )
    final_activation = desktop.rindex("activate_desktop_units")
    rollback_disarm = desktop.index("ROLLBACK_ARMED=false", transaction_complete)
    lease_release = desktop.index(
        "release_browser_maintenance",
        transaction_complete,
    )
    assert final_activation < transaction_complete < rollback_disarm < lease_release


def test_boot_reconciler_resumes_durable_committed_rollback_intent() -> None:
    state = _source("scripts/release-state.py")
    reconcile = _source("scripts/reconcile-platform-release.sh")

    intent_write = state.index('intent["rollback_requested_at"]')
    pointer_restore = state.index(
        '_atomic_symlink(root / "active-state", relative_target)',
        intent_write,
    )
    assert intent_write < pointer_restore
    assert "rollback_requested_at" in reconcile
    assert 'run_bounded "resume_committed_rollback"' in reconcile
    assert '"$SCRIPT_DIR/release-state.py" rollback-commit' in reconcile


def test_release_coordinators_reconcile_on_term_and_int_signals() -> None:
    server = _source("scripts/server-platform-release.sh")
    desktop = _source("scripts/platform-desktop-release.sh")

    assert "ROLLBACK_IN_PROGRESS=false" in server
    assert "ROLLBACK_IN_PROGRESS=false" in desktop
    assert "trap 'handle_termination 143' TERM" in server
    assert "trap 'handle_termination 130' INT" in server
    assert 'rollback_parent_release "$exit_code"' in server
    assert "trap 'handle_termination 143' TERM" in desktop
    assert "trap 'handle_termination 130' INT" in desktop
    assert 'rollback "$exit_code"' in desktop


def test_browser_money_commands_scope_client_keys_at_the_verified_principal_boundary() -> None:
    operator = _source("apps/api/routers/v1/operator.py")
    campaigns = _source("apps/api/routers/v1/campaigns_create.py")

    for source in (operator, campaigns):
        assert "principal_scoped_idempotency_key(" in source
        assert "principal=requested_by" in source
        assert "client_key=idempotency_key" in source
        assert "idempotency_key=scoped_idempotency_key" in source
