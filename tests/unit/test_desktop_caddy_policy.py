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
    public_desktop = desktop.split("desktop.adpulse.su {", maxsplit=1)[1].split(
        "http://desktop.localhost:8099", maxsplit=1
    )[0]
    assert "stream_timeout 1m" in public_desktop
    assert "stream_timeout 30m" not in public_desktop
    assert "owner revoke is enforced on reconnect within 60s" in public_desktop
    assert "uri /desktop-auth/verify" in app
    assert "header_up -Remote-User" in app
    assert "header_up -Authorization" in app
    assert "header_up -Connection" in app
    assert "header_up -Upgrade" in app
    assert "guacamole" not in (app + desktop).lower()
    assert "127.0.0.1:8090" not in app + desktop


def test_only_session_endpoints_and_authenticated_kasm_are_public_on_desktop_host() -> None:
    config = DESKTOP_SITE.read_text(encoding="utf-8")
    redeem = config.split("handle /desktop-auth/redeem", maxsplit=1)[1].split(
        "handle /desktop-auth/verify", maxsplit=1
    )[0]
    verify = config.split("handle /desktop-auth/verify", maxsplit=1)[1].split(
        "handle /desktop-auth/profile", maxsplit=1
    )[0]
    profile = config.split("handle /desktop-auth/profile", maxsplit=1)[1].split(
        "handle /desktop/logout", maxsplit=1
    )[0]

    assert "log_skip" in redeem
    assert "reverse_proxy 127.0.0.1:18100" in redeem
    assert "header_up -Authorization" in redeem
    assert "respond 404" in verify
    assert "reverse_proxy 127.0.0.1:18100" in profile
    assert "header_up -Authorization" in profile
    assert "header_up -X-API-Key" in profile
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
        assert "reverse_proxy 127.0.0.1:18100" in readiness
        assert "import panel_session_auth" in readiness


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
