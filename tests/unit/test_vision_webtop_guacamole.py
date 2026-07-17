from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEBTOP = ROOT / "deploy" / "vision-webtop"
COMPOSE = WEBTOP / "compose.yaml"
DOCKERFILE = WEBTOP / "Dockerfile"
BOOTSTRAP = WEBTOP / "bootstrap-guacamole-db.sh"
VNC_RUN = WEBTOP / "vision-vnc-run"
WINDOW_FIT = WEBTOP / "vision-window-fit-run"
INSTALLER = ROOT / "scripts" / "install-vision-webtop.sh"
DESKTOP_AUTH_ROUTER = ROOT / "apps" / "api" / "routers" / "desktop_auth.py"
EXTENSION_MANIFEST = WEBTOP / "guacamole-extension" / "guac-manifest.json"
EXTENSION_MENU = WEBTOP / "guacamole-extension" / "html" / "user-menu.html"


def test_production_stack_is_digest_pinned_and_has_no_public_selkies() -> None:
    source = COMPOSE.read_text(encoding="utf-8")

    assert 'DISPLAY_WIDTH: "1366"' in source
    assert 'DISPLAY_HEIGHT: "768"' in source
    assert 'DISPLAY_DPI: "96"' in source
    assert "DESKTOP_WEBTOP_IMAGE:?" in source
    assert "guacamole/guacd@sha256:" in source
    assert "guacamole/guacamole@sha256:" in source
    assert "postgres:16-alpine@sha256:" in source
    assert "\n  postgres:" not in source
    assert "\n  guacamole-postgres:" in source
    assert '"127.0.0.1:8090:8080"' in source
    assert '"127.0.0.1:3000:3000"' not in source
    assert '"127.0.0.1:3001:3001"' not in source
    assert '"5900:5900"' not in source
    assert '"4822:4822"' not in source
    assert "JSON_ENABLED" not in source
    assert "JSON_SECRET" not in source


def test_guacamole_uses_header_auth_and_dedicated_jdbc() -> None:
    source = COMPOSE.read_text(encoding="utf-8")

    assert 'HTTP_AUTH_ENABLED: "true"' in source
    assert "HTTP_AUTH_HEADER: Remote-User" in source
    assert 'POSTGRESQL_ENABLED: "true"' in source
    assert 'POSTGRESQL_AUTO_CREATE_ACCOUNTS: "false"' in source
    assert "DESKTOP_GUACAMOLE_POSTGRES_PASSWORD" in source
    assert "WEBAPP_CONTEXT: desktop" in source
    assert "guacamole-postgres:/var/lib/postgresql/data" in source
    assert "condition: service_completed_successfully" in source
    assert "vision-guacamole" in source
    assert "vision-guacamole-db" in source
    assert "vision-webtop" in source
    assert "adpulse-desktop-navigation.jar:/etc/guacamole/extensions/" in source


def test_guacamole_menu_has_one_session_revoking_return_action() -> None:
    manifest = EXTENSION_MANIFEST.read_text(encoding="utf-8")
    menu = EXTENSION_MENU.read_text(encoding="utf-8")

    assert '"guacamoleVersion": "1.6.0"' in manifest
    assert 'name="replace"' in menu
    assert ".user-menu guac-menu > ul.action-list:last-child" in menu
    assert 'form method="post" action="/desktop/logout"' in menu
    assert "Вернуться в панель" in menu


def test_readyz_proves_header_auth_instead_of_accepting_shell_html() -> None:
    source = DESKTOP_AUTH_ROUTER.read_text(encoding="utf-8")

    assert "client.post(" in source
    assert 'f"{endpoint}/api/tokens"' in source
    assert '"Remote-User": DESKTOP_PRINCIPAL' in source
    assert 'payload.get("username") == DESKTOP_PRINCIPAL' in source
    assert 'client.delete(f"{endpoint}/api/tokens/' in source


def test_database_bootstrap_is_idempotent_and_enforces_single_read_connection() -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8")

    assert "partial or incompatible Guacamole schema" in source
    assert "guacamole-schema.sql" in source
    assert "adpulse-desktop" in source
    assert "DELETE FROM guacamole_system_permission" in source
    assert "DELETE FROM guacamole_connection WHERE connection_name <> 'Vision Desktop'" in source
    assert "('hostname', '127.0.0.1')" in source
    assert "('port', '5900')" in source
    assert "('width', '1366')" in source
    assert "('height', '768')" in source
    assert "('disable-display-resize', 'true')" in source
    assert "('read-only', 'false')" in source
    assert "permission <> 'READ'" in source


def test_installer_readiness_requires_loopback_vnc_contract() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert """parameter_value = '"'"'127.0.0.1'"'"'""" in source
    assert """parameter_value = '"'"'vision-webtop'"'"'""" not in source


def test_webtop_exports_existing_x11_display_through_tigervnc() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    service = VNC_RUN.read_text(encoding="utf-8")

    assert "tigervnc-scraping-server" in dockerfile
    assert "tigervnc-tools" in dockerfile
    assert "svc-vision-vnc" in dockerfile
    assert "selkies-clipboard-bridge" not in dockerfile
    assert "exec X0tigervnc" in service
    assert '-display "$DISPLAY"' in service
    assert "-localhost" in service
    assert "-AlwaysShared" in service
    assert "-PasswordFile" in service


def test_webtop_keeps_isolated_vision_window_maximizer() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    watcher = WINDOW_FIT.read_text(encoding="utf-8")

    assert "ubuntu-xfce@sha256:" in dockerfile
    assert "wmctrl" in dockerfile
    assert "svc-vision-window-fit" in dockerfile
    assert "/config/.local/share/Vision/profiles/" in watcher
    assert "add,maximized_vert,maximized_horz" in watcher


def test_installer_is_change_aware_and_checks_full_desktop_contract() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    assert 'readonly COMPOSE_ENV_FILE="$PROJECT_DIR/.env"' in installer
    assert "stat -Lc '%a'" in installer
    assert "DESKTOP_WEBTOP_IMAGE must be an immutable image@sha256 reference" in installer
    assert "ACTIVE_MANIFEST_FILE" in installer
    assert "MANIFEST_CHANGED=false" in installer
    assert "compose pull" in installer
    assert 'docker image inspect "$image"' in installer
    assert "missing_image" in installer
    assert "STACK_MUTATED=true" in installer
    assert "compose rm -sf guacamole guacd database-bootstrap" in installer
    assert "compose build" not in installer
    assert "--force-recreate" not in installer
    assert "service_is_healthy webtop" in installer
    assert "service_is_healthy guacd" in installer
    assert "service_is_healthy guacamole-postgres" in installer
    assert "service_is_healthy guacamole" in installer
    assert "bootstrap_completed" in installer
    assert "database_contract_is_ready" in installer
    assert "http://127.0.0.1:8090/desktop/" in installer
    assert 'chmod 0644 "$TARGET_DIR/adpulse-desktop-navigation.jar"' in installer
    assert "/dev/tcp/127.0.0.1/4822" in installer
    assert "/dev/tcp/127.0.0.1/5900" in installer
    assert 'docker rm -f "$BROWSER_AGENT_CONTAINER"' in installer
    assert "ensure_cdp_ready" in installer
