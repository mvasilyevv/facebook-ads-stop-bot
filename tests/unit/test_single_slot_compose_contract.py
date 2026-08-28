from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "compose"


def _source(name: str) -> str:
    return (COMPOSE / name).read_text(encoding="utf-8")


def test_compose_projects_are_stable_single_slot_projects() -> None:
    assert "name: fb_agent_infra\n" in _source("docker-compose.infra.yml")
    assert "name: fb_agent_app\n" in _source("docker-compose.app.yml")
    assert "name: fb_agent_app\n" in _source("docker-compose.jobs.yml")
    assert "name: fb_agent_desktop\n" in _source("docker-compose.desktop-agent.yml")


def test_app_plane_has_no_release_jobs_or_color_contract() -> None:
    source = _source("docker-compose.app.yml")

    for forbidden in (
        "APP_COLOR",
        "FB_AGENT_DEPLOYMENT_COLOR",
        "ADOPTION_BUNDLE_FILE",
        "adoption_importer:",
        "migrator:",
        "telegram_webhook_configurator:",
    ):
        assert forbidden not in source


def test_release_jobs_are_isolated_from_long_running_services() -> None:
    source = _source("docker-compose.jobs.yml")

    assert "migrator:" in source
    assert "adoption_importer:" in source
    assert "adoption_status:" in source
    assert "database_check:" in source
    assert "runtime_config_bootstrap:" in source
    assert "vision_config_bootstrap:" in source
    assert "${VISION_BOOTSTRAP_ENV_FILE:?set VISION_BOOTSTRAP_ENV_FILE}" in source
    assert "telegram_webhook_configurator:" in source
    assert "${ADOPTION_BUNDLE_FILE:-/dev/null}" in source
    for forbidden in ("api:", "frontend:", "mini-app:", "observer:"):
        assert forbidden not in source


def test_infra_has_no_pgbackrest_or_archive_contract() -> None:
    source = _source("docker-compose.infra.yml").lower()

    for forbidden in (
        "pgbackrest",
        "archive_mode",
        "archive_command",
        "backup_env_file",
    ):
        assert forbidden not in source


def test_desktop_plane_owns_vision_and_browser_agent() -> None:
    source = _source("docker-compose.desktop-agent.yml")

    assert "vision-webtop:" in source
    assert "browser-agent:" in source
    assert "DESKTOP_WEBTOP_IMAGE" in source
    assert "BROWSER_AGENT_IMAGE" in source
    assert "${VISION_CONFIG_DIR:?" in source
    assert "env_file: ${DESKTOP_ENV_FILE:?" in source
    assert "env_file: ${APP_ENV_FILE:?" not in source
    assert 'network_mode: "service:vision-webtop"' in source
    assert "condition: service_healthy" in source


def test_retired_backup_and_reconciliation_units_are_absent() -> None:
    retired = (
        "deploy/backup/pgbackrest.conf",
        "deploy/backup/pgbackrest.env.example",
        "deploy/systemd/fb-agent-pgbackrest-diff.service",
        "deploy/systemd/fb-agent-pgbackrest-diff.timer",
        "deploy/systemd/fb-agent-pgbackrest-full.service",
        "deploy/systemd/fb-agent-pgbackrest-full.timer",
        "deploy/systemd/fb-agent-restore-drill.service",
        "deploy/systemd/fb-agent-restore-drill.timer",
        "deploy/systemd/fb-agent-release-reconcile.service",
    )

    for relative_path in retired:
        assert not (ROOT / relative_path).exists(), relative_path
