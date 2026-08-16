import re
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
        "-X-Operator-Principal",
        "-X-Verified-Operator-Principal",
        "-X-Panel-Telegram-User-Id",
        "-X-Panel-Role",
        "-Connection",
        "-Upgrade",
    ):
        assert f"header_up {header}" in panel_auth
    assert "copy_headers X-Verified-Operator-Principal" in panel_auth
    for path in ("/api/*", "/ws/*"):
        route = public.split(f"handle {path}", maxsplit=1)[1].split("handle ", maxsplit=1)[0]
        assert "import panel_session_auth" in route
        assert "header_up X-API-Key {$API_KEY}" in route
        for header in (
            "-Remote-User",
            "-Authorization",
            "-Proxy-Authorization",
            "-X-Operator-Principal",
            "-X-Panel-Telegram-User-Id",
            "-X-Panel-Role",
            "-X-Desktop-Telegram-User-Id",
            "-X-Desktop-Role",
        ):
            assert f"header_up {header}" in route

    panel_ws = public.split("handle /ws/*", maxsplit=1)[1].split("handle ", maxsplit=1)[0]
    assert "stream_timeout 1m" in panel_ws
    assert "PostgreSQL owner check at least once per minute" in panel_ws


def test_api_trust_boundaries_strip_client_operator_principal() -> None:
    config = _config()
    blocks = re.findall(
        r"(?:forward_auth|reverse_proxy) 127\.0\.0\.1:18100 \{(.*?)^\s*\}",
        config,
        re.MULTILINE | re.DOTALL,
    )
    assert len(blocks) == 18
    assert all("header_up -X-Operator-Principal" in block for block in blocks)


def test_browser_authority_consume_is_exact_post_only_and_header_scoped() -> None:
    public = _public(_config())
    matcher_start = public.index("@browser_authority_consume {")
    route_start = public.index("handle @browser_authority_consume", matcher_start)
    route_end = public.index("# Vision recovery", route_start)
    generic_api_start = public.index("handle /api/*", route_start)
    matcher = public[matcher_start:route_start]
    route = public[route_start:route_end]

    assert matcher_start < route_start < generic_api_start
    assert "method POST" in matcher
    assert "path /api/v1/internal/browser-operations/consume" in matcher
    assert "/api/v1/internal/browser-operations/*" not in public
    assert "log_skip" in route
    assert 'header Cache-Control "no-store"' in route
    assert "import panel_session_auth" not in route
    assert "header_up X-API-Key {$API_KEY}" not in route
    for header in (
        "Remote-User",
        "Authorization",
        "Proxy-Authorization",
        "X-API-Key",
        "X-Operator-Principal",
        "X-Verified-Operator-Principal",
        "X-Panel-Telegram-User-Id",
        "X-Panel-Role",
        "X-Desktop-Telegram-User-Id",
        "X-Desktop-Role",
        "X-Telegram-Init-Data",
        "Cookie",
    ):
        assert f"header_up -{header}" in route
    assert (
        "header_up X-Browser-Authority-Token {http.request.header.X-Browser-Authority-Token}"
    ) in route
    assert "header_up -X-Browser-Authority-Token" not in route

    generic_api = public[generic_api_start:]
    assert "import panel_session_auth" in generic_api


def test_browser_maintenance_consume_is_exact_post_only_and_header_scoped() -> None:
    public = _public(_config())
    matcher_start = public.index("@browser_maintenance_consume {")
    route_start = public.index("handle @browser_maintenance_consume", matcher_start)
    route_end = public.index("# Telegram Mini App", route_start)
    generic_api_start = public.index("handle /api/*", route_start)
    matcher = public[matcher_start:route_start]
    route = public[route_start:route_end]

    assert matcher_start < route_start < generic_api_start
    assert "method POST" in matcher
    assert "path /api/v1/internal/browser-maintenance/consume" in matcher
    assert "/api/v1/internal/browser-maintenance/*" not in public
    assert "log_skip" in route
    assert 'header Cache-Control "no-store"' in route
    assert "import panel_session_auth" not in route
    assert "header_up X-API-Key {$API_KEY}" not in route
    for header in (
        "Remote-User",
        "Authorization",
        "Proxy-Authorization",
        "X-API-Key",
        "X-Operator-Principal",
        "X-Verified-Operator-Principal",
        "X-Panel-Telegram-User-Id",
        "X-Panel-Role",
        "X-Desktop-Telegram-User-Id",
        "X-Desktop-Role",
        "X-Telegram-Init-Data",
        "Cookie",
    ):
        assert f"header_up -{header}" in route
    assert (
        "header_up X-Browser-Authority-Token {http.request.header.X-Browser-Authority-Token}"
    ) in route
    assert "header_up -X-Browser-Authority-Token" not in route

    generic_api = public[generic_api_start:]
    assert "import panel_session_auth" in generic_api


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


def test_tma_navigation_capabilities_are_not_logged_cached_or_referred() -> None:
    public = _public(_config())
    route = public.split("handle /tma*", maxsplit=1)[1].split("handle ", maxsplit=1)[0]
    assert "log_skip" in route
    assert 'header Cache-Control "no-store"' in route
    assert 'header Referrer-Policy "no-referrer"' in route


def test_breakglass_is_loopback_basic_auth_with_full_panel_proxy() -> None:
    """Breakglass остаётся только у панели: веб-канала стола больше нет,
    аварийный доступ к столу — по SSH и нативному каналу."""
    config = _config()
    breakglass = config.split("http://127.0.0.1:8099", maxsplit=1)[1]
    assert "bind 127.0.0.1" in breakglass
    assert "import breakglass_auth" in breakglass
    assert "reverse_proxy 127.0.0.1:18080" in breakglass
    assert "reverse_proxy 127.0.0.1:18100" in breakglass
    api_and_ws = breakglass.split("handle {", maxsplit=1)[0]
    assert api_and_ws.count("header_up -X-Verified-Operator-Principal") == 2
