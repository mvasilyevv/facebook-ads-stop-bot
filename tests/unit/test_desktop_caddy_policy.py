from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_desktop_uses_server_session_and_keeps_basic_auth_hidden() -> None:
    config = (ROOT / "deploy/caddy/desktop.adpulse.su.caddy").read_text(encoding="utf-8")

    recovery = config.split("handle /desktop-auth/recovery", maxsplit=1)[1].split(
        "handle /desktop-auth/redeem", maxsplit=1
    )[0]
    redeem = config.split("handle /desktop-auth/redeem", maxsplit=1)[1].split(
        "handle /desktop-auth/*", maxsplit=1
    )[0]
    desktop = config.split("handle {", maxsplit=1)[1]
    auth = config.split("(desktop_session_auth)", maxsplit=1)[1].split(
        "desktop.adpulse.su", maxsplit=1
    )[0]

    assert "output file /var/log/caddy/fb-agent-access.log" in config
    assert "header_up -Connection" in auth
    assert "header_up -Upgrade" in auth
    assert "import desktop_recovery_auth" in recovery
    assert "basic_auth" not in redeem
    assert "log_skip" in redeem
    assert "import desktop_session_auth" in desktop
    assert "reverse_proxy 127.0.0.1:3000" in desktop
    assert "stream_close_delay 5m" in desktop


def test_guacamole_canary_is_owner_only_and_grants_are_not_logged() -> None:
    config = (ROOT / "deploy/caddy/desktop.adpulse.su.caddy").read_text(encoding="utf-8")

    grant = config.split("handle @guacamole_grant", maxsplit=1)[1].split(
        "handle /guacamole/*", maxsplit=1
    )[0]
    canary = config.split("handle /guacamole/*", maxsplit=1)[1].split("handle {", maxsplit=1)[0]
    fallback = config.rsplit("handle {", maxsplit=1)[1]

    assert "query data=*" in config
    assert "log_skip" in grant
    assert 'header Cache-Control "no-store"' in grant
    assert "import desktop_session_auth" in grant
    assert "reverse_proxy 127.0.0.1:8090" in grant
    assert "header_up -Authorization" in grant
    assert "header_up -Proxy-Authorization" in grant
    assert "header_up -X-Panel-Recovery-Key" in grant
    assert "import desktop_session_auth" in canary
    assert "reverse_proxy 127.0.0.1:8090" in canary
    assert "reverse_proxy 127.0.0.1:3000" in fallback


def test_installer_migrates_legacy_desktop_block_before_validation() -> None:
    installer = (ROOT / "scripts/install-server-units.sh").read_text(encoding="utf-8")

    install_position = installer.index("deploy/caddy/desktop.adpulse.su.caddy")
    remove_position = installer.index("scripts/remove-caddy-site-block.py")
    validate_position = installer.index("caddy validate --config")
    assert install_position < remove_position < validate_position
    assert (
        'readonly DESKTOP_CADDY_SITE="/etc/caddy/sites-enabled/desktop.adpulse.su.caddy"'
        in installer
    )


def test_panel_launch_response_with_ticket_is_not_access_logged() -> None:
    config = (ROOT / "deploy/caddy/app.adpulse.su.caddy").read_text(encoding="utf-8")
    launch = config.split("handle /auth/desktop/launch", maxsplit=1)[1].split(
        "handle /tma*", maxsplit=1
    )[0]

    assert "log_skip" in launch
    assert "import panel_auth" in launch
    assert "rewrite * /desktop-auth/launch-recovery" in launch
    assert "reverse_proxy 127.0.0.1:8100" in launch
    assert "header_up X-Panel-Recovery-Key {$API_KEY}" in launch
    assert "header_up -Authorization" in launch
    assert "header_up -Proxy-Authorization" in launch


def test_panel_keeps_basic_auth_for_existing_surfaces() -> None:
    config = (ROOT / "deploy/caddy/app.adpulse.su.caddy").read_text(encoding="utf-8")

    assert "(panel_auth)" in config
    assert "telegram_panel_auth" not in config
    assert config.count("import panel_auth") >= 4
