from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_SITE = ROOT / "deploy" / "caddy" / "app.adpulse.su.caddy"


# Единственный desktop-канал: same-origin /desktop/* за forward_auth, Guacamole
# с фиксированным Remote-User и потолком жизни WS-туннеля (ревокация ≤ 30 минут).
def test_desktop_is_same_origin_header_authenticated_guacamole_only() -> None:
    config = APP_SITE.read_text(encoding="utf-8")
    desktop = config.split("handle /desktop/*", maxsplit=1)[1].split(
        "handle /desktop-readyz", maxsplit=1
    )[0]
    auth = config.split("(desktop_session_auth)", maxsplit=1)[1].split(
        "app.adpulse.su", maxsplit=1
    )[0]

    assert "desktop.adpulse.su" not in config
    assert "import desktop_session_auth" in desktop
    assert "reverse_proxy 127.0.0.1:8090" in desktop
    assert "stream_timeout 30m" in desktop
    assert 'header_up Remote-User "adpulse-desktop"' in desktop
    # Caddy применяет header_up-удаление ПОСЛЕ установки: строка
    # `header_up -Remote-User` рядом с set стёрла бы только что выставленный
    # заголовок, и Guacamole получал бы запрос без Remote-User (форма логина
    # вместо header-auth). Set сам перезаписывает любой клиентский Remote-User,
    # поэтому отдельный strip не нужен и вреден.
    assert "header_up -Remote-User" not in desktop
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


# /desktop-readyz performs the real Guacamole/VNC handshake and therefore stays
# behind the owner panel session; monitoring can still call the API on loopback.
def test_desktop_readiness_is_separate_and_requires_panel_session() -> None:
    config = APP_SITE.read_text(encoding="utf-8")
    readiness = config.split("handle /desktop-readyz", maxsplit=1)[1].split(
        "handle /api/*", maxsplit=1
    )[0]

    assert "reverse_proxy 127.0.0.1:8100" in readiness
    assert "import panel_session_auth" in readiness


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


def test_public_panel_uses_forward_auth_and_basic_auth_is_loopback_only() -> None:
    config = APP_SITE.read_text(encoding="utf-8")
    public = config.split("app.adpulse.su {", maxsplit=1)[1].split(
        "http://127.0.0.1:8099", maxsplit=1
    )[0]
    breakglass = config.split("http://127.0.0.1:8099", maxsplit=1)[1]

    assert "basic_auth" not in public
    assert public.count("import panel_session_auth") >= 4
    assert "uri /auth/verify" in config
    assert "bind 127.0.0.1" in breakglass
    assert "import breakglass_auth" in breakglass
    assert (
        "basic_auth"
        in config.split("(breakglass_auth)", maxsplit=1)[1].split(
            "(panel_session_auth)", maxsplit=1
        )[0]
    )
