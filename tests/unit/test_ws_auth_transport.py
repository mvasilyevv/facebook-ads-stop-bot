from __future__ import annotations

from types import SimpleNamespace

from apps.api.routers.ws import _websocket_api_key


def test_websocket_prefers_caddy_injected_header() -> None:
    websocket = SimpleNamespace(
        headers={"x-api-key": "server-only"},
        query_params={"api_key": "legacy-query"},
    )

    assert _websocket_api_key(websocket) == "server-only"


def test_websocket_keeps_direct_client_query_fallback() -> None:
    websocket = SimpleNamespace(headers={}, query_params={"api_key": "direct-client"})

    assert _websocket_api_key(websocket) == "direct-client"


def test_websocket_missing_credentials_is_empty() -> None:
    websocket = SimpleNamespace(headers={}, query_params={})

    assert _websocket_api_key(websocket) == ""
