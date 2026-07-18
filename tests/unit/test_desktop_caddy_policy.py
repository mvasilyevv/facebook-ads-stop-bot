from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_SITE = ROOT / "deploy" / "caddy" / "app.adpulse.su.caddy"
DESKTOP_SITE = ROOT / "deploy" / "caddy" / "desktop.adpulse.su.caddy"


def test_public_desktop_is_cookie_authenticated_kasm_only() -> None:
    app = APP_SITE.read_text(encoding="utf-8")
    desktop = DESKTOP_SITE.read_text(encoding="utf-8")

    assert "desktop.adpulse.su {" in desktop
    assert "import desktop_session_auth" in desktop
    assert "reverse_proxy 127.0.0.1:8444" in desktop
    assert 'header_up Authorization "Basic {$DESKTOP_KASM_SERVICE_AUTH_B64}"' in desktop
    assert "stream_timeout 30m" in desktop
    assert "uri /desktop-auth/verify" in app
    assert "header_up -Remote-User" in app
    assert "header_up -Authorization" in app
    assert "header_up -Connection" in app
    assert "header_up -Upgrade" in app
    assert "guacamole" not in (app + desktop).lower()
    assert "127.0.0.1:8090" not in app + desktop


def test_only_redeem_logout_and_internal_verify_are_public_on_desktop_host() -> None:
    config = DESKTOP_SITE.read_text(encoding="utf-8")
    redeem = config.split("handle /desktop-auth/redeem", maxsplit=1)[1].split(
        "handle /desktop-auth/verify", maxsplit=1
    )[0]
    verify = config.split("handle /desktop-auth/verify", maxsplit=1)[1].split(
        "handle /desktop/logout", maxsplit=1
    )[0]

    assert "log_skip" in redeem
    assert "reverse_proxy 127.0.0.1:8100" in redeem
    assert "header_up -Authorization" in redeem
    assert "respond 404" in verify
    assert "handle /desktop/logout" in config
    for removed in (
        "/desktop-auth/connect",
        "/desktop-auth/recovery",
        "/desktop-auth/launch-recovery",
        "/auth/desktop/session",
    ):
        assert removed not in config


def test_both_readiness_routes_require_panel_session() -> None:
    config = APP_SITE.read_text(encoding="utf-8")
    for route in ("/desktop-readyz", "/desktop-kasm-readyz"):
        readiness = config.split(f"handle {route}", maxsplit=1)[1].split("handle /", maxsplit=1)[0]
        assert "reverse_proxy 127.0.0.1:8100" in readiness
        assert "import panel_session_auth" in readiness


def test_installer_atomically_installs_and_restores_desktop_site() -> None:
    installer = (ROOT / "scripts" / "install-server-units.sh").read_text(encoding="utf-8")

    assert 'DESKTOP_CADDY_SITE="/etc/caddy/sites-enabled/desktop.adpulse.su.caddy"' in installer
    assert 'install -m 0644 "$PROJECT_DIR/deploy/caddy/desktop.adpulse.su.caddy"' in installer
    assert 'cp -- "$TEMP_DIR/desktop-site.caddy" "$DESKTOP_CADDY_SITE"' in installer
    assert "caddy validate --config" in installer


def test_basic_auth_is_loopback_breakglass_only() -> None:
    app = APP_SITE.read_text(encoding="utf-8")
    desktop = DESKTOP_SITE.read_text(encoding="utf-8")
    public_panel = app.split("app.adpulse.su {", maxsplit=1)[1].split(
        "http://127.0.0.1:8099", maxsplit=1
    )[0]
    public_desktop = desktop.split("desktop.adpulse.su {", maxsplit=1)[1].split(
        "http://desktop.localhost:8099", maxsplit=1
    )[0]

    assert "basic_auth" not in public_panel
    assert "basic_auth" not in public_desktop
    assert "http://127.0.0.1:8099" in app
    assert "http://desktop.localhost:8099" in desktop
    assert "bind 127.0.0.1" in app
    assert "bind 127.0.0.1" in desktop
    assert "import breakglass_auth" in app
    assert "import breakglass_auth" in desktop
