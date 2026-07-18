from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "deploy" / "caddy" / "app.adpulse.su.caddy"


def _config() -> str:
    return SITE.read_text(encoding="utf-8")


def _public(config: str) -> str:
    return config.split("app.adpulse.su {", maxsplit=1)[1].split(
        "http://127.0.0.1:8099", maxsplit=1
    )[0]


def test_public_panel_api_and_websocket_use_cookie_forward_auth() -> None:
    config = _config()
    public = _public(config)
    panel_auth = config.split("(panel_session_auth)", maxsplit=1)[1].split(
        "(desktop_session_auth)", maxsplit=1
    )[0]
    assert "basic_auth" not in public
    assert "uri /auth/verify" in panel_auth
    for header in (
        "-Remote-User",
        "-Authorization",
        "-Proxy-Authorization",
        "-X-API-Key",
        "-X-Panel-Telegram-User-Id",
        "-X-Panel-Role",
        "-Connection",
        "-Upgrade",
    ):
        assert f"header_up {header}" in panel_auth
    for path in ("/api/*", "/ws/*"):
        route = public.split(f"handle {path}", maxsplit=1)[1].split("handle ", maxsplit=1)[0]
        assert "import panel_session_auth" in route
        assert "header_up X-API-Key {$API_KEY}" in route
        for header in (
            "-Remote-User",
            "-Authorization",
            "-Proxy-Authorization",
            "-X-Panel-Telegram-User-Id",
            "-X-Panel-Role",
            "-X-Desktop-Telegram-User-Id",
            "-X-Desktop-Role",
        ):
            assert f"header_up {header}" in route


def test_oidc_and_one_time_ticket_routes_are_narrow_and_not_logged() -> None:
    config = _config()
    public = _public(config)
    for route in (
        "/auth/login",
        "/auth/telegram/start",
        "/auth/telegram/callback",
        "/auth/redeem",
        "/auth/logout",
    ):
        assert f"handle {route}" in public
    for route in ("/auth/telegram/callback", "/auth/redeem"):
        segment = public.split(f"handle {route}", maxsplit=1)[1].split("handle ", maxsplit=1)[0]
        assert "log_skip" in segment
        assert "header_up -Authorization" in segment
        assert "header_up -X-API-Key" in segment
    verify = public.split("handle /auth/verify", maxsplit=1)[1].split("handle ", maxsplit=1)[0]
    assert "respond 404" in verify
    assert "/auth/recovery" not in config


def test_breakglass_is_loopback_basic_auth_with_full_panel_and_desktop_proxy() -> None:
    config = _config()
    breakglass = config.split("http://127.0.0.1:8099", maxsplit=1)[1]
    assert "bind 127.0.0.1" in breakglass
    assert "import breakglass_auth" in breakglass
    assert "reverse_proxy 127.0.0.1:8080" in breakglass
    assert "reverse_proxy 127.0.0.1:8100" in breakglass
    assert "reverse_proxy 127.0.0.1:8090" in breakglass
    assert 'header_up Remote-User "adpulse-desktop"' in breakglass
