import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_desktop_contract_cutover_follows_app_switch_while_claims_are_fenced() -> None:
    script = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")

    candidate_up = script.index('"$SCRIPT_DIR/platform-alloy-agent.sh" candidate-up')
    maintenance_enter = script.index("browser_maintenance_enter", candidate_up)
    application_release = script.index('"$SCRIPT_DIR/bluegreen-deploy.sh"', maintenance_enter)
    desktop_release = script.index(
        '"$SCRIPT_DIR/platform-desktop-release.sh"',
        application_release,
    )
    maintenance_leave = script.index("browser_maintenance_leave", desktop_release)

    assert maintenance_enter < application_release < desktop_release < maintenance_leave
    assert "desktop release is intentionally not rolled back" not in script


def test_ci_desktop_digests_flow_only_through_the_immutable_manifest() -> None:
    script = (ROOT / "scripts/deploy-platform-server.sh").read_text(encoding="utf-8")
    manifest = (ROOT / "scripts/create-release-manifest.sh").read_text(encoding="utf-8")
    desktop_release = (ROOT / "scripts/platform-desktop-release.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "DESKTOP_WEBTOP_IMAGE" in manifest
    assert "DESKTOP_KASMVNC_IMAGE" in manifest
    assert "--desktop-webtop-image" in manifest
    assert "--desktop-kasmvnc-image" in manifest
    assert "desktop_webtop_image" in script
    assert "desktop_kasmvnc_image" in script
    assert "BROWSER_AGENT_IMAGE" in desktop_release
    assert "docker-compose.desktop-agent.yml" in desktop_release
    assert "build-desktop:" in workflow
    assert "deploy/vision-webtop/Dockerfile" in workflow
    assert "deploy/kasmvnc-sidecar/Dockerfile" in workflow
    assert "${{ steps.build_webtop.outputs.digest }}" in workflow
    assert "${{ steps.build_kasm.outputs.digest }}" in workflow
    assert (
        '--desktop-webtop-image "${{ needs.build-desktop.outputs.webtop_image_ref }}"' in workflow
    )
    assert '--desktop-kasmvnc-image "${{ needs.build-desktop.outputs.kasm_image_ref }}"' in workflow
    assert (
        "DESKTOP_WEBTOP_IMAGE: ${{ needs.build-desktop.outputs.webtop_image_ref }}" not in workflow
    )
    assert (
        "DESKTOP_KASMVNC_IMAGE: ${{ needs.build-desktop.outputs.kasm_image_ref }}" not in workflow
    )
    assert "DOCKER_CONFIG='$DEPLOY_DOCKER_CONFIG' docker login ghcr.io" in workflow


def test_candidate_env_is_not_activated_until_bluegreen_commit() -> None:
    deploy = (ROOT / "scripts/deploy-platform-server.sh").read_text(encoding="utf-8")
    release = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")

    assert "$REMOTE_STAGING/production.env" in deploy
    assert 'remote_lock_publish "$REMOTE_STAGING" "$remote_release"' in deploy
    assert "$ROOT_DIR/shared/.env.new" not in deploy
    prepare = release.index('"$SCRIPT_DIR/release-state.py" prepare')
    begin = release.index('"$SCRIPT_DIR/release-state.py" begin')
    bluegreen = release.index('"$SCRIPT_DIR/bluegreen-deploy.sh"')
    assert prepare < begin < bluegreen
    assert '--candidate-state "$CANDIDATE_STATE"' in release
    assert "--previous-app-env" not in release
    assert "mv -Tf" not in release

    child = (ROOT / "scripts/bluegreen-deploy.sh").read_text(encoding="utf-8")
    accepted = child.index("--stage accepted")
    commit = child.index("commit --state-root")
    links = child.index("ensure-links")
    rollback_disarmed = child.index("trap - ERR", links)
    assert accepted < commit < links < rollback_disarmed
    assert "sync-desired-env" not in child
    assert 'release-state.py" complete' not in child

    alloy = release.index("--stage alloy_adopted", bluegreen)
    timers = release.index("--stage timers_adopted", alloy)
    systemd = release.index("--stage systemd_adopted", timers)
    desktop = release.index("--stage desktop_adopted", systemd)
    complete = release.index('release-state.py" complete', desktop)
    assert bluegreen < alloy < timers < systemd < desktop < complete


def test_caddy_credentials_follow_their_independent_cutover_boundaries() -> None:
    release = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")
    desktop = (ROOT / "scripts/platform-desktop-release.sh").read_text(encoding="utf-8")
    desktop_transaction = (ROOT / "scripts/platform-desktop-transaction.sh").read_text(
        encoding="utf-8"
    )
    bluegreen = (ROOT / "scripts/bluegreen-deploy.sh").read_text(encoding="utf-8")
    switch = (ROOT / "scripts/bluegreen-switch-caddy.sh").read_text(encoding="utf-8")
    reconciler = (ROOT / "scripts/reconcile-platform-release.sh").read_text(encoding="utf-8")

    assert "--caddy-only --sync-scope none" in release
    assert "platform-desktop-transaction.sh" in desktop
    assert "--caddy-only --sync-scope desktop" in desktop_transaction
    assert '--app-env "$APP_ENV"' in bluegreen
    assert '--app-env "$PREVIOUS_APP_ENV"' not in bluegreen
    assert 'active_app_env="$(state --source "$active_source" --field app_env)"' in reconciler
    assert '--app-env "$active_app_env"' in reconciler
    assert "--scope api" in switch
    assert 'install -m 0600 "$TEMP_DIR/caddy.previous.env" "$CADDY_ENV_FILE"' in switch


def test_runtime_uses_committed_app_and_desktop_env_snapshots() -> None:
    app_runtime = (ROOT / "scripts/platform-compose.sh").read_text(encoding="utf-8")
    desktop_runtime = (ROOT / "scripts/platform-desktop-compose.sh").read_text(encoding="utf-8")
    desktop_release = (ROOT / "scripts/platform-desktop-release.sh").read_text(encoding="utf-8")
    server_release = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")
    agent_unit = (ROOT / "deploy/systemd/fb-agent-desktop-agent.service").read_text(
        encoding="utf-8"
    )
    healer_unit = (ROOT / "deploy/systemd/fb-agent-desktop-heal.service").read_text(
        encoding="utf-8"
    )

    assert 'APP_STATE_DIR="$FB_AGENT_ACTIVE_STATE_DIR"' in app_runtime
    assert 'APP_ENV="$APP_STATE_DIR/app.env"' in app_runtime
    assert 'ACTIVE_STATE="$FB_AGENT_ACTIVE_STATE_DIR"' in desktop_runtime
    assert 'APP_ENV="$active_state_dir/app.env"' in desktop_runtime
    assert 'RELEASE_ENV="$active_state_dir/release-images.env"' in desktop_runtime
    assert 'readlink -f "$active_state_dir/release"' in desktop_runtime
    assert '"desktop-states/${CANDIDATE_STATE##*/}" "$ACTIVE_DESKTOP_STATE"' in desktop_release
    assert "active-desktop.env" not in desktop_runtime
    assert "active-desktop-release-images.env" not in desktop_runtime
    assert "active_desktop_release_dir" in server_release
    assert 'canonical" != "$active_desktop_release_dir"' in server_release
    verifier = (
        "/usr/local/libexec/fb-agent-release-verifier/current/"
        "verified-release-exec.py --state desktop"
    )
    for unit in (agent_unit, healer_unit):
        assert verifier in unit
        assert "/opt/fb-agent/shared/active-desktop-state/release/" not in unit


def test_production_secrets_never_transit_the_github_runner() -> None:
    deploy = (ROOT / "scripts/deploy-platform-server.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "PROD_ENV_B64" not in workflow
    assert 'rsync -a "$TARGET:$ROOT_DIR/shared/.env"' not in deploy
    assert 'python3 "$SCRIPT_DIR/prepare_production_env.py"' not in deploy
    assert "python3 '$REMOTE_STAGING/scripts/prepare_production_env.py'" in deploy
    assert "provision-bootstrap-secrets.py" in deploy
    assert "--bootstrap-secrets '$ROOT_DIR/shared/bootstrap-secrets.env'" in deploy
    assert "--generate-postgres-password-if-insecure" not in deploy


def test_deploy_is_fail_closed_to_the_single_canonical_public_origin() -> None:
    deploy = (ROOT / "scripts/deploy-platform-server.sh").read_text(encoding="utf-8")

    assert 'readonly CANONICAL_PUBLIC_URL="https://app.adpulse.su"' in deploy
    assert "only the canonical public URL" in deploy
    assert "--public-url '$CANONICAL_PUBLIC_URL'" in deploy
    assert "--input '$ROOT_DIR/shared/.env'" in deploy


def test_deploy_uses_a_preprovisioned_pinned_ssh_host_key() -> None:
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "DEPLOY_KNOWN_HOSTS: ${{ secrets.DEPLOY_KNOWN_HOSTS }}" in workflow
    assert "StrictHostKeyChecking yes" in workflow
    assert 'ssh-keygen -F "$DEPLOY_HOST"' in workflow
    assert "ssh-keyscan" not in workflow


def test_release_workflow_dependencies_are_immutable() -> None:
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    action_refs = re.findall(r"^\s*uses:\s+[^@\s]+@([^\s#]+)", workflow, re.MULTILINE)
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
    assert re.search(r"image: postgres:16@sha256:[0-9a-f]{64}", workflow)
    assert re.search(r"image: redis:7-alpine@sha256:[0-9a-f]{64}", workflow)
