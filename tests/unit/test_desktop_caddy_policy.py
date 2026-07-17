from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_SITE = ROOT / "deploy" / "caddy" / "app.adpulse.su.caddy"


def test_desktop_is_same_origin_header_authenticated_guacamole_only() -> None:
    config = APP_SITE.read_text(encoding="utf-8")
    desktop = config.split("handle /desktop/*", maxsplit=1)[1].split("handle /tma*", maxsplit=1)[0]
    auth = config.split("(desktop_session_auth)", maxsplit=1)[1].split(
        "app.adpulse.su", maxsplit=1
    )[0]

    assert "desktop.adpulse.su" not in config
    assert "import desktop_session_auth" in desktop
    assert "reverse_proxy 127.0.0.1:8090" in desktop
    assert 'header_up Remote-User "adpulse-desktop"' in desktop
    assert "header_up -Authorization" in desktop
    assert "header_up -X-API-Key" in desktop
    assert "uri /desktop-auth/verify" in auth
    assert "header_up -Remote-User" in auth
    assert "header_up -Connection" in auth
    assert "header_up -Upgrade" in auth
    assert "query data=*" not in config
    assert "127.0.0.1:3000" not in config


def test_only_redeem_logout_and_internal_verify_routes_remain() -> None:
    config = APP_SITE.read_text(encoding="utf-8")
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
        "/desktop-auth/launch-url-recovery",
        "/auth/desktop/session",
        "/auth/desktop/launch",
    ):
        assert removed not in config


def test_desktop_readiness_is_separate_from_bot_readiness() -> None:
    config = APP_SITE.read_text(encoding="utf-8")
    readiness = config.split("handle /desktop-readyz", maxsplit=1)[1].split(
        "handle /api/v1/postback/*", maxsplit=1
    )[0]

    assert "reverse_proxy 127.0.0.1:8100" in readiness
    assert "import panel_auth" not in readiness


def test_installer_atomically_removes_the_obsolete_desktop_site() -> None:
    installer = (ROOT / "scripts" / "install-server-units.sh").read_text(encoding="utf-8")

    assert (
        'OBSOLETE_DESKTOP_CADDY_SITE="/etc/caddy/sites-enabled/desktop.adpulse.su.caddy"'
        in installer
    )
    assert 'rm -f -- "$OBSOLETE_DESKTOP_CADDY_SITE"' in installer
    assert (
        'cp -- "$TEMP_DIR/obsolete-desktop-site.caddy" "$OBSOLETE_DESKTOP_CADDY_SITE"' in installer
    )
    assert "remove-caddy-site-block.py" not in installer


def test_panel_keeps_basic_auth_for_existing_surfaces() -> None:
    config = APP_SITE.read_text(encoding="utf-8")

    assert "(panel_auth)" in config
    assert "telegram_panel_auth" not in config
    assert config.count("import panel_auth") >= 3
