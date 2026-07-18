from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_caddy_skips_postback_access_log() -> None:
    config = (ROOT / "deploy/caddy/app.adpulse.su.caddy").read_text(encoding="utf-8")
    route = config.split("handle /api/v1/postback/*", maxsplit=1)[1].split(
        "handle /api/tma/*", maxsplit=1
    )[0]

    assert "log_skip" in route
    assert "reverse_proxy 127.0.0.1:8100" in route
    assert "header_up -X-API-Key" in route
    websocket_route = config.split("handle /ws/*", maxsplit=1)[1].split("handle /tma*", maxsplit=1)[
        0
    ]
    assert "log_skip" in websocket_route


def test_caddy_keeps_master_key_server_side_and_preserves_tma_auth() -> None:
    config = (ROOT / "deploy/caddy/app.adpulse.su.caddy").read_text(encoding="utf-8")

    tma_public = config.split("handle /api/tma/*", maxsplit=1)[1].split(
        "@tma_bearer_api", maxsplit=1
    )[0]
    assert "panel_session_auth" not in tma_public
    assert "{$API_KEY}" not in tma_public
    assert "header_up -X-API-Key" in tma_public

    bearer_matcher = config.split("@tma_bearer_api", maxsplit=1)[1].split(
        "handle @tma_bearer_api", maxsplit=1
    )[0]
    assert "path /api/*" in bearer_matcher
    assert 'header Authorization "Bearer *"' in bearer_matcher
    bearer_route = config.split("handle @tma_bearer_api", maxsplit=1)[1].split(
        "handle /auth/login", maxsplit=1
    )[0]
    assert "panel_session_auth" not in bearer_route
    assert "{$API_KEY}" not in bearer_route
    assert "header_up -X-API-Key" in bearer_route

    desktop_api = config.split("handle /api/*", maxsplit=1)[1].split("handle /ws/*", maxsplit=1)[0]
    assert "import panel_session_auth" in desktop_api
    assert "header_up X-API-Key {$API_KEY}" in desktop_api

    websocket_route = config.split("handle /ws/*", maxsplit=1)[1].split("handle /tma*", maxsplit=1)[
        0
    ]
    assert "import panel_session_auth" in websocket_route
    assert "header_up X-API-Key {$API_KEY}" in websocket_route


def test_uvicorn_access_log_is_disabled_for_all_entrypoints() -> None:
    assert "access_log=False" in (ROOT / "run_api.py").read_text(encoding="utf-8")
    for path in (ROOT / "Makefile", ROOT / "run.sh", ROOT / "docker/Dockerfile.api"):
        assert "--no-access-log" in path.read_text(encoding="utf-8")
