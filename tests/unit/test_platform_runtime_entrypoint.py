from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_systemd_cannot_resurrect_an_unsupported_compose_runtime() -> None:
    unit = (ROOT / "deploy/systemd/fb-agent.service").read_text(encoding="utf-8")
    runtime = (ROOT / "scripts/platform-compose.sh").read_text(encoding="utf-8")

    assert "platform-compose.sh" in unit
    assert "server-compose.sh" not in unit
    assert "docker-compose.yml" not in runtime
    assert "docker-compose.app.yml" in runtime
    assert "docker-compose.infra.yml" in runtime
    assert "telegram_poller" not in runtime
    assert "telegram_delivery_worker" in runtime
    assert "telegram_update_worker" in runtime


def test_boot_runtime_requires_active_digest_manifest() -> None:
    runtime = (ROOT / "scripts/platform-compose.sh").read_text(encoding="utf-8")
    assert 'APP_STATE_DIR="$FB_AGENT_ACTIVE_STATE_DIR"' in runtime
    assert 'RELEASE_ENV="$APP_STATE_DIR/release-images.env"' in runtime
    assert 'COLOR_FILE="$APP_STATE_DIR/color"' in runtime
    assert "--profile workers" in runtime


def test_backup_timers_require_the_explicit_restore_acceptance_gate() -> None:
    general_installer = (ROOT / "scripts/install-server-units.sh").read_text(encoding="utf-8")
    backup_installer = (ROOT / "scripts/install-platform-units.sh").read_text(encoding="utf-8")

    activation = general_installer.split("systemctl enable", maxsplit=1)[1]
    assert "fb-agent-pgbackrest-full.timer" not in activation
    assert "fb-agent-pgbackrest-diff.timer" not in activation
    assert "fb-agent-restore-drill.timer" not in activation
    assert "fb-agent-pgbackrest-full.timer" in backup_installer
    assert "fb-agent-pgbackrest-diff.timer" in backup_installer
    assert "fb-agent-restore-drill.timer" in backup_installer
    release = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")
    assert "install-platform-units.sh" in release
    assert "--expected-release-id" in release
    assert "--verify-only" in release


def test_caddy_switch_moves_reverse_proxy_and_forward_auth_together() -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/bluegreen-switch-caddy.sh"),
            "--color",
            "green",
            "--site-file",
            str(ROOT / "deploy/caddy/app.adpulse.su.caddy"),
            "--desktop-site-file",
            str(ROOT / "deploy/caddy/desktop.adpulse.su.caddy"),
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("+\tforward_auth 127.0.0.1:28100") == 2
    assert "+\t\t\treverse_proxy 127.0.0.1:28100" in result.stdout


def test_telegram_webhook_bypasses_only_operator_session_auth() -> None:
    caddy = (ROOT / "deploy/caddy/app.adpulse.su.caddy").read_text(encoding="utf-8")
    webhook = caddy.index("handle /api/v1/integrations/telegram/webhook")
    operator_api = caddy.index("handle /api/*")
    block = caddy[webhook : caddy.index("# Alertmanager has", webhook)]

    assert webhook < operator_api
    assert "panel_session_auth" not in block
    assert "header_up -X-API-Key" in block
    assert "X-Telegram-Bot-Api-Secret-Token" not in block


def test_alertmanager_webhook_preserves_only_its_scoped_bearer() -> None:
    caddy = (ROOT / "deploy/caddy/app.adpulse.su.caddy").read_text(encoding="utf-8")
    webhook = caddy.index("handle /api/v1/integrations/alertmanager/webhook")
    operator_api = caddy.index("handle /api/*")
    block = caddy[webhook : caddy.index("# Browser-agent consumes", webhook)]

    assert webhook < operator_api
    assert "panel_session_auth" not in block
    assert "header_up -Authorization" not in block
    assert "header_up -X-API-Key" in block


def test_restore_postmaster_keeps_pgbackrest_s3_recovery_context() -> None:
    restore = (ROOT / "scripts/pgbackrest-restore-drill.sh").read_text(encoding="utf-8")
    postmaster = restore.split('docker run -d --name "$CONTAINER"', maxsplit=1)[1].split(
        "for _ in", maxsplit=1
    )[0]

    assert '--env-file "$BACKUP_ENV"' in postmaster
    assert '"$CONFIG_FILE:/etc/pgbackrest/pgbackrest.conf:ro"' in postmaster


def test_worker_handoff_allows_only_explicit_maintenance_pause_blockers() -> None:
    handoff = (ROOT / "scripts/bluegreen-worker-handoff.sh").read_text(encoding="utf-8")

    assert 'data.get("scanning_enabled") is False' in handoff
    assert '"scanning_paused"' in handoff
    assert '"observer_runtime_paused"' not in handoff
    assert '"meta_api_channel_unknown"' not in handoff
    assert "blockers.issubset(allowed)" in handoff


def test_redis_outage_is_degraded_not_a_platform_boot_blocker() -> None:
    runtime = (ROOT / "scripts/platform-compose.sh").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts/platform-bootstrap.sh").read_text(encoding="utf-8")
    release = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")
    handoff = (ROOT / "scripts/bluegreen-worker-handoff.sh").read_text(encoding="utf-8")

    assert "up -d --wait --wait-timeout 240 postgres" in runtime
    assert "up -d --wait --wait-timeout 240 postgres" in bootstrap
    assert "optional Redis" in runtime
    assert "optional Redis" in bootstrap
    assert "optional Redis" in release
    assert 'die "durable Redis is not running"' not in release
    assert "target_singletons_ready" in handoff
    assert "Redis is unavailable" not in handoff
    assert 'data.get("actors_expected")' in runtime
    assert 'data.get("actors_expected")' in handoff
    assert 'data.get("redis_available") is True' not in runtime
    assert 'data.get("redis_available") is True' not in handoff


def test_platform_bootstrap_uses_only_owned_safety_first_resources() -> None:
    bootstrap = (ROOT / "scripts/platform-bootstrap.sh").read_text(encoding="utf-8")
    network_inventory = (ROOT / "scripts/platform-network-inventory.py").read_text(encoding="utf-8")
    infra = (ROOT / "deploy/compose/docker-compose.infra.yml").read_text(encoding="utf-8")
    restore = (ROOT / "scripts/pgbackrest-restore-drill.sh").read_text(encoding="utf-8")

    for source in (bootstrap, infra, restore):
        assert "fb_agent_safety_first_pgdata" in source
        assert "fb_agent_pgdata" not in source
    for expected in (
        "fb_agent_safety_first_redisdata",
        "fb_agent_safety_first_pgbackrest_spool",
    ):
        assert expected in bootstrap
        assert expected in infra
    assert "fb_agent_safety_first_campaign_uploads" in bootstrap
    assert "fb_agent_safety_first_platform" in bootstrap
    assert "fb_agent_safety_first_platform" in infra
    for retired in (
        "fb_agent_redisdata",
        "fb_agent_pgbackrest_spool",
        "fb_agent_campaign_uploads",
        "fb_agent_default",
    ):
        assert retired not in bootstrap
        assert retired not in infra
    assert "com.fb-agent.cluster-id" in bootstrap
    assert "com.fb-agent.network-contract=safety-first-v1" in bootstrap
    assert "com.fb-agent.volume-purpose" in bootstrap
    assert "platform-network-inventory.py" in bootstrap
    assert "platform network contains an unowned endpoint" in network_inventory
    assert "owns protected alias" in network_inventory
    # The schema guard is generated from one Python contract; the deployment
    # shell must not carry a stale third copy of catalog identities.
    assert "DO $fresh_target_guard$" not in bootstrap
    assert "standalone_types AS" not in bootstrap
    assert "baseline_contract.py" in bootstrap
    assert "--render-platform-psql-guard" in bootstrap
    assert "shared safety-first database guard" in bootstrap
    assert "bootstrap-state.py" in bootstrap


def test_all_platform_compose_defaults_use_the_single_safety_first_network() -> None:
    compose_paths = (
        "deploy/compose/docker-compose.infra.yml",
        "deploy/monitoring/docker-compose.agent.yml",
        "deploy/monitoring/docker-compose.local-app.yml",
        "deploy/vision-webtop/compose.yaml",
    )
    for path in compose_paths:
        source = (ROOT / path).read_text(encoding="utf-8")
        assert "fb_agent_safety_first_platform" in source, path
        assert "fb_agent_default" not in source, path


def test_runtime_config_is_imported_once_before_candidate_services_start() -> None:
    migrator = (ROOT / "scripts/run-migrations-locked.py").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/bluegreen-deploy.sh").read_text(encoding="utf-8")
    delivery = (ROOT / "apps/telegram_delivery_worker/main.py").read_text(encoding="utf-8")
    menu_button = (ROOT / "core/telegram/menu_button.py").read_text(encoding="utf-8")
    telegram_service = (ROOT / "core/telegram/service.py").read_text(encoding="utf-8")
    settings_router = (ROOT / "apps/api/routers/v1/settings_telegram.py").read_text(
        encoding="utf-8"
    )

    assert "await bootstrap_telegram_config_from_env(runtime_engine)" in migrator
    assert "await bootstrap_adsetpro_credentials_from_env(runtime_engine)" in migrator
    assert "await bootstrap_web_app_url_from_env(runtime_engine)" in migrator
    assert "hide_parameters=True" in migrator
    assert migrator.index('("check",)') < migrator.index(
        "await bootstrap_telegram_config_from_env(runtime_engine)"
    )
    runtime_loader = telegram_service[
        telegram_service.index("async def load_telegram_config") : telegram_service.index(
            "@dataclass(frozen=True)\nclass Recipient"
        )
    ]
    assert "get_settings" not in runtime_loader
    assert "bootstrap_telegram_config_from_env" not in runtime_loader
    assert deploy.index("--profile migration run --rm migrator") < deploy.index(
        "up -d --no-deps --wait --wait-timeout 240 api frontend mini-app"
    )
    for runtime_source in (delivery, menu_button, settings_router):
        assert "get_settings().web_app_url" not in runtime_source
        assert "settings.web_app_url" not in runtime_source


def test_scheduled_workers_use_postgres_singleton_ownership() -> None:
    launchers = (
        "run_cleanup_worker.py",
        "run_reconciler_worker.py",
        "run_health_watchdog.py",
        "run_digest_scheduler.py",
    )
    for launcher in launchers:
        source = (ROOT / launcher).read_text(encoding="utf-8")
        assert "run_postgres_singleton(" in source, launcher

    handoff = (ROOT / "scripts/bluegreen-worker-handoff.sh").read_text(encoding="utf-8")
    assert "/tmp/fb-agent-postgres-singleton-" in handoff
    assert "target_singletons_ready" in handoff


def test_telegram_webhook_cutover_is_part_of_the_committed_release() -> None:
    deploy = (ROOT / "scripts/bluegreen-deploy.sh").read_text(encoding="utf-8")
    compose = (ROOT / "deploy/compose/docker-compose.app.yml").read_text(encoding="utf-8")

    public_health = deploy.index('"${PUBLIC_URL%/}/healthz"')
    route_probe = deploy.index("platform-route-probe-invalid", public_health)
    alertmanager_probe = deploy.index("public Alertmanager webhook route probe", route_probe)
    webhook = deploy.index("telegram_webhook_configurator", alertmanager_probe)
    accepted = deploy.index("--stage accepted", webhook)
    release_commit = deploy.index('commit --state-root "$STATE_DIR"', accepted)
    rollback_disarmed = deploy.index("trap - ERR", release_commit)
    old_web_stop = deploy.index('docker compose -p "fb_agent_${PREVIOUS_COLOR}"', rollback_disarmed)

    assert (
        public_health
        < route_probe
        < alertmanager_probe
        < webhook
        < accepted
        < release_commit
        < rollback_disarmed
        < old_web_stop
    )
    assert "sync-desired-env" not in deploy
    assert 'release-state.py" complete' not in deploy

    parent = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")
    parent_cutover = parent.index('"$SCRIPT_DIR/bluegreen-deploy.sh"')
    transaction_complete = parent.index(
        'release-state.py" complete',
        parent_cutover,
    )
    desktop_adopted = parent.index("--stage desktop_adopted", parent_cutover)
    parent_disarmed = parent.index("ROLLBACK_ARMED=false", transaction_complete)
    assert parent_cutover < desktop_adopted < transaction_complete < parent_disarmed
    assert '"/api/v1/integrations/telegram/webhook"' in deploy
    assert '"/api/v1/integrations/alertmanager/webhook"' in deploy
    assert "profiles: [release]" in compose
    assert "/app/scripts/configure-telegram-webhook.py" in compose


def test_candidate_caddy_route_is_installed_before_any_release_mutation() -> None:
    release = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")
    installer = (ROOT / "scripts/install-server-units.sh").read_text(encoding="utf-8")

    caddy = release.index('"$SCRIPT_DIR/install-server-units.sh"')
    desktop_preflight = release.index(
        '"$SCRIPT_DIR/platform-desktop-release.sh"',
        caddy,
    )
    app = release.index('"$SCRIPT_DIR/bluegreen-deploy.sh"')
    desktop = release.index('"$SCRIPT_DIR/platform-desktop-release.sh"', app)
    caddy_only_exit = installer.index('if [[ "$CADDY_ONLY" == true ]]')
    app_unit_enable = installer.index("systemctl enable", caddy_only_exit)

    assert caddy < desktop_preflight < app < desktop
    assert "--preflight-only" in release[desktop_preflight:app]
    assert "--caddy-only --sync-scope none" in release[caddy:desktop_preflight]
    assert caddy_only_exit < app_unit_enable


def test_release_hup_stops_the_complete_supervised_child_group_first(
    tmp_path: Path,
) -> None:
    release = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")

    def function(name: str, following: str) -> str:
        start = release.index(f"{name}() {{")
        end = release.index(f"\n}}\n\n{following}() {{", start) + 3
        return release[start:end]

    events = tmp_path / "events"
    pids = tmp_path / "pids"
    child = tmp_path / "blocking-child.sh"
    child.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"trap 'printf \"child-stopped\\\\n\" >>{events!s}; exit 143' TERM HUP\n"
        "sleep 300 &\n"
        "grandchild=$!\n"
        f'printf \'%s %s\\n\' "$$" "$grandchild" >{pids!s}\n'
        'wait "$grandchild"\n',
        encoding="utf-8",
    )
    child.chmod(0o755)
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        'CUTOVER_DEADLINE_EPOCH=""\n'
        'ACTIVE_CHILD_PID=""\n'
        'ACTIVE_CHILD_PGID=""\n'
        "die() { exit 99; }\n"
        + function("cutover_remaining_seconds", "run_cutover_bounded")
        + "\n"
        + function("run_supervised_child", "terminate_supervised_child")
        + "\n"
        + function("terminate_supervised_child", "classify_desktop_release_outcome")
        + "\n"
        + f'trap \'terminate_supervised_child; printf "parent-after-child\\\\n" '
        f">>{events!s}; exit 129' HUP\n"
        + f"(for _ in {{1..100}}; do [[ -s {pids!s} ]] && break; "
        + "sleep 0.05; done; kill -HUP $$) &\n"
        + f'run_supervised_child "{child!s}"\n',
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        ["bash", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 129, result.stderr
    assert events.read_text(encoding="utf-8").splitlines() == [
        "child-stopped",
        "parent-after-child",
    ]
    for pid in (int(value) for value in pids.read_text().split()):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise AssertionError(f"supervised release descendant {pid} survived HUP")


def test_failed_cutover_reconciles_the_single_atomic_state_pointer() -> None:
    deploy = (ROOT / "scripts/bluegreen-deploy.sh").read_text(encoding="utf-8")
    rollback = deploy[deploy.index("rollback() {") : deploy.index("trap rollback ERR")]

    assert "reconcile-platform-release.sh" in rollback
    assert "CUTOVER_BUDGET_SECONDS=180" in deploy
    assert '--deadline-epoch "$CUTOVER_DEADLINE_EPOCH"' in rollback
    assert "--signal=KILL" in rollback
    assert "mark-rollback-failed" in rollback
    assert "active-release-images.env" not in rollback
    assert "active-color" not in rollback
    assert "|| true" not in rollback


def test_parent_orchestrator_passes_exact_atomic_candidate_contract() -> None:
    release = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")
    invocation = release.split('"$SCRIPT_DIR/bluegreen-deploy.sh"', maxsplit=1)[1].split(
        "\n\ntrap - ERR", maxsplit=1
    )[0]

    for argument in (
        '--color "$target_color"',
        '--release-env "$candidate_release_env"',
        '--app-env "$candidate_app_env"',
        '--backup-env "$BACKUP_ENV"',
        '--state-dir "$STATE_DIR"',
        '--candidate-state "$CANDIDATE_STATE"',
        "--activate",
    ):
        assert argument in invocation
    assert "--previous-app-env" not in release
    assert 'mv -f -- "$CANDIDATE' not in release
    assert 'ln -sfn "$RELEASE_DIR"' not in release


def test_cutover_uses_one_durable_absolute_deadline_end_to_end() -> None:
    deploy = (ROOT / "scripts" / "bluegreen-deploy.sh").read_text(encoding="utf-8")
    handoff = (ROOT / "scripts" / "bluegreen-worker-handoff.sh").read_text(encoding="utf-8")
    reconcile = (ROOT / "scripts" / "reconcile-platform-release.sh").read_text(encoding="utf-8")
    parent = (ROOT / "scripts" / "server-platform-release.sh").read_text(encoding="utf-8")

    arm = deploy.index("arm-cutover")
    initial_blue = deploy.index("select-initial", arm)
    route = deploy.index("bluegreen-switch-caddy.sh", arm)
    assert arm < initial_blue < route
    assert 'if [[ -z "$PREVIOUS_COLOR" ]]' in deploy[arm:route]
    assert "recovery_policy" in reconcile
    assert "restore_committed_telegram_webhook" in reconcile
    assert deploy.count('--deadline-epoch "$CUTOVER_DEADLINE_EPOCH"') >= 2
    assert "--deadline-epoch) DEADLINE_EPOCH=" in handoff
    assert "DEADLINE_EPOCH - now" in handoff
    assert 'handoff+=(--deadline-epoch "$DEADLINE_EPOCH")' in reconcile
    assert "cutover_deadline_epoch" in reconcile
    assert "FB_AGENT_PARENT_RECONCILES" not in deploy
    assert "FB_AGENT_PARENT_RECONCILES" not in parent
    assert "if ((exit_code == 70))" in parent


def test_boot_recovery_preserves_an_expired_cutover_deadline_as_evidence() -> None:
    reconcile = (ROOT / "scripts" / "reconcile-platform-release.sh").read_text(encoding="utf-8")

    breach = reconcile.index("if ((DEADLINE_EPOCH <= now_epoch))")
    convergence = reconcile.index("active_source=active", breach)
    recovery_start = reconcile.index("start_deadline_breach_recovery() {")
    recovery_end = reconcile.index("\n}\n\nstage_rank() {", recovery_start)
    recovery = reconcile[recovery_start:recovery_end]
    assert breach < convergence
    assert 'ORIGINAL_CUTOVER_DEADLINE="$original_deadline"' in recovery
    assert "BOOT_RECOVERY_DEADLINE=true" in recovery
    assert "DEADLINE_EPOCH=$((now_epoch + DEADLINE_SECONDS))" in recovery
    assert '"$BOOT_MODE" != true' in reconcile[breach:convergence]
    assert '"$DEADLINE_EXPLICIT" == true' in reconcile[breach:convergence]
    assert "cutover_deadline_breached:original_" in recovery
    assert "mark-rollback-failed" in recovery
    assert "start_deadline_breach_recovery" in reconcile[breach:convergence]
    assert "arm-cutover" not in reconcile


def test_money_handoff_starts_target_before_stopping_incumbent_money_last() -> None:
    handoff = (ROOT / "scripts" / "bluegreen-worker-handoff.sh").read_text(encoding="utf-8")

    target_non_money = handoff.index('up -d --no-deps --remove-orphans "${NON_MONEY_WORKERS[@]}"')
    target_money = handoff.index('up -d --no-deps "${MONEY_WORKERS[@]}"')
    target_ready = handoff.index("wait_target_money_ready", target_money)
    old_non_money = handoff.index(
        'stop --timeout "$stop_timeout" "${NON_MONEY_WORKERS[@]}"', target_ready
    )
    old_money = handoff.index('stop --timeout "$stop_timeout" "${MONEY_WORKERS[@]}"', old_non_money)
    removed_worker_cleanup = handoff.index(
        'retire_removed_worker_containers "$FROM_COLOR"',
        old_money,
    )

    assert (
        target_non_money
        < target_money
        < target_ready
        < old_non_money
        < old_money
        < removed_worker_cleanup
    )
    assert "com.fb-agent.release" in handoff
    assert '"$release" == "$target_release_id"' in handoff
    assert "label=com.fb-agent.metrics=true" in handoff
    assert '[[ "$service" == "api" ]] && continue' in handoff
    assert "redis-cli --raw --scan" not in handoff
    assert "worker:heartbeat:" not in handoff


def test_tma_operator_websocket_has_narrow_backend_validated_bypass() -> None:
    caddy = (ROOT / "deploy/caddy/app.adpulse.su.caddy").read_text(encoding="utf-8")
    matcher = caddy.index("@tma_operator_ws {")
    bypass = caddy.index("handle @tma_operator_ws", matcher)
    generic_ws = caddy.index("handle /ws/*", bypass)
    block = caddy[bypass : caddy.index("# Public Telegram OIDC endpoints", bypass)]

    assert matcher < bypass < generic_ws
    assert "path /ws/operator" in caddy[matcher:bypass]
    assert "header_regexp tma_protocol Sec-WebSocket-Protocol" in caddy[matcher:bypass]
    assert "tma\\." in caddy[matcher:bypass]
    assert "log_skip" in block
    assert "import panel_session_auth" not in block
    for header in (
        "X-API-Key",
        "X-Operator-Principal",
        "Remote-User",
        "Authorization",
        "Proxy-Authorization",
        "X-Panel-Telegram-User-Id",
        "X-Panel-Role",
    ):
        assert f"header_up -{header}" in block
    assert "Sec-WebSocket-Protocol" not in block


def test_release_manifest_preserves_registry_ports(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == "buildx version" ]]; then exit 0; fi\n'
        "printf 'Digest: sha256:%064d\\n' 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    output = tmp_path / "release.env"
    digest = "sha256:" + "1" * 64
    env = os.environ | {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "REDIS_SOURCE_IMAGE": f"registry.example:5000/redis@{digest}",
    }

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/create-release-manifest.sh"),
            "--image-base",
            "registry.example:5000/fb-agent",
            "--tag",
            "release-1",
            "--output",
            str(output),
            "--desktop-webtop-image",
            f"registry.example:5000/webtop@{digest}",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    manifest = output.read_text(encoding="utf-8")
    assert "API_IMAGE=registry.example:5000/fb-agent-api@sha256:" in manifest
    assert f"DESKTOP_WEBTOP_IMAGE=registry.example:5000/webtop@{digest}" in manifest
