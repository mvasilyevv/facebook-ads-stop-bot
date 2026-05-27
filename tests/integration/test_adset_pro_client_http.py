# -*- coding: utf-8 -*-
"""Интеграционный: AdsetProClient → реальный HTTP через httpx + respx (без живого AdSet.pro).

Проверяет правильность сериализации запроса (заголовки, MCP envelope, args) и
обработку HTTP-статусов. Все запросы идут через POST /mcp (JSON-RPC 2.0) —
старый REST вида /api/stats/query не существует у AdSet.pro (live verify 2026-05-27).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
import respx
from httpx import Response, TransportError

from core.adset_pro import (
    AdsetProClient,
    AuthError,
    NotFoundError,
    RateLimitedError,
    StatsQueryRequest,
)
from core.adset_pro.errors import TemporaryError

_BASE_URL = "https://adset.pro.test"
_MCP_URL = f"{_BASE_URL}/mcp"


def _make_client(**overrides) -> AdsetProClient:
    """Маленький helper — клиент без зависимости от глобальных settings."""
    params: dict = {
        "api_key": "mcp_test_key",
        "base_url": _BASE_URL,
        "timeout_seconds": 1.0,
        "max_retries": 2,
    }
    params.update(overrides)
    return AdsetProClient(**params)


def _mcp_initialize_response() -> dict:
    """Стандартный ответ MCP-сервера на initialize."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "platform-stats-mcp", "version": "1.0.0"},
        },
    }


def _mcp_tool_response(structured: dict, *, rpc_id: int = 1) -> dict:
    """Обернуть structuredContent в полный JSON-RPC ответ tools/call."""
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(structured)}],
            "structuredContent": structured,
        },
    }


# health_check шлёт JSON-RPC initialize и возвращает True при 200 с валидным result.
@pytest.mark.asyncio
async def test_health_check_returns_true_on_200() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_MCP_URL).mock(return_value=Response(200, json=_mcp_initialize_response()))
        async with _make_client() as client:
            assert await client.health_check() is True


# 5xx на /mcp → health_check False (без exception, чтобы можно было опрашивать в health-watchdog).
@pytest.mark.asyncio
async def test_health_check_returns_false_on_5xx() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_MCP_URL).mock(return_value=Response(503, text="down"))
        async with _make_client() as client:
            assert await client.health_check() is False


# Сетевой сбой → health_check False (для деградированного режима без алертов).
@pytest.mark.asyncio
async def test_health_check_returns_false_on_network_error() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_MCP_URL).mock(side_effect=TransportError("dns fail"))
        async with _make_client() as client:
            assert await client.health_check() is False


# query_stats шлёт JSON-RPC tools/call с Bearer и корректными MCP arguments.
@pytest.mark.asyncio
async def test_query_stats_sends_bearer_token_and_payload() -> None:
    captured: dict = {}

    def _handler(request):
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content) if request.content else {}
        return Response(
            200,
            json=_mcp_tool_response(
                {
                    "data": [
                        {
                            "click_id": "c1",
                            "ext_sub6": "ad-1",
                            "event_type": "ftd",
                            "revenue": "10.50",
                            "currency": "EUR",
                            "occurred_at": "2026-05-15T10:00:00Z",
                        }
                    ]
                }
            ),
        )

    with respx.mock(assert_all_called=True) as mock:
        mock.post(_MCP_URL).mock(side_effect=_handler)
        async with _make_client() as client:
            resp = await client.query_stats(
                StatsQueryRequest(
                    since=date(2026, 5, 1),
                    until=date(2026, 5, 15),
                    ad_id="ad-1",
                )
            )

    # Авторизация Bearer + правильный Accept (json или SSE).
    assert captured["headers"].get("authorization") == "Bearer mcp_test_key"
    assert "application/json" in captured["headers"].get("accept", "")
    # JSON-RPC envelope + ссылка на нужный MCP tool.
    body = captured["body"]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "query_stats"
    args = body["params"]["arguments"]
    assert args["from"] == "2026-05-01"
    assert args["to"] == "2026-05-15"
    # ad_id мэтчится по ext_sub6 в фильтре MCP.
    assert {"field": "ext_sub6", "op": "eq", "value": "ad-1"} in args["filters"]

    # Ответ распарсился корректно.
    assert len(resp.rows) == 1
    row = resp.rows[0]
    assert row.click_id == "c1"
    assert row.fb_ad_id == "ad-1"
    assert row.event_type == "ftd"
    assert row.revenue == Decimal("10.50")
    assert row.currency == "EUR"


# 401 на /mcp → AuthError, без retry (permanent).
@pytest.mark.asyncio
async def test_query_stats_401_raises_auth_error_without_retry() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(_MCP_URL).mock(return_value=Response(401, json={"error": "Invalid key"}))
        async with _make_client() as client:
            with pytest.raises(AuthError) as info:
                await client.query_stats(
                    StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 2))
                )
    assert info.value.status_code == 401
    assert route.call_count == 1


# 404 на /mcp → NotFoundError (permanent).
@pytest.mark.asyncio
async def test_query_stats_404_raises_not_found() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_MCP_URL).mock(return_value=Response(404, text="no such endpoint"))
        async with _make_client() as client:
            with pytest.raises(NotFoundError):
                await client.query_stats(
                    StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 2))
                )


# 429 на /mcp → RateLimitedError, ретраи доходят до max_retries.
@pytest.mark.asyncio
async def test_query_stats_429_retries_then_raises() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(_MCP_URL).mock(return_value=Response(429, text="slow down"))
        async with _make_client(max_retries=2) as client:
            with pytest.raises(RateLimitedError):
                await client.query_stats(
                    StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 2))
                )
    assert route.call_count == 2


# 5xx один раз → 200 на второй → клиент успешно возвращает ответ.
@pytest.mark.asyncio
async def test_query_stats_retries_5xx_then_succeeds() -> None:
    responses = [
        Response(500, text="boom"),
        Response(200, json=_mcp_tool_response({"data": []})),
    ]

    def _handler(request):
        return responses.pop(0)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(_MCP_URL).mock(side_effect=_handler)
        async with _make_client(max_retries=3) as client:
            resp = await client.query_stats(
                StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 2))
            )

    assert route.call_count == 2
    assert resp.rows == ()


# list_conversions — сахар поверх query_stats; парсит structuredContent.data.
@pytest.mark.asyncio
async def test_list_conversions_returns_parsed_rows() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.post(_MCP_URL).mock(
            return_value=Response(
                200,
                json=_mcp_tool_response(
                    {
                        "data": [
                            {"click_id": "1", "ext_sub6": "ad-1", "revenue": "5"},
                            {"click_id": "2", "ext_sub6": "ad-1", "revenue": "7.5"},
                        ]
                    }
                ),
            )
        )
        async with _make_client() as client:
            rows = await client.list_conversions(
                since=date(2026, 5, 1),
                until=date(2026, 5, 10),
                ad_id="ad-1",
            )

    assert len(rows) == 2
    assert {r.click_id for r in rows} == {"1", "2"}
    assert all(r.fb_ad_id == "ad-1" for r in rows)


# 200 с битым JSON → TemporaryError после max_retries.
@pytest.mark.asyncio
async def test_query_stats_invalid_json_raises_temporary() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_MCP_URL).mock(return_value=Response(200, content=b"<<<broken>>>"))
        async with _make_client(max_retries=2) as client:
            with pytest.raises(TemporaryError):
                await client.query_stats(
                    StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 2))
                )


# call_mcp_tool вытаскивает structuredContent корректно (для будущих AI-tools).
@pytest.mark.asyncio
async def test_call_mcp_tool_returns_structured_content() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.post(_MCP_URL).mock(
            return_value=Response(
                200,
                json=_mcp_tool_response({"items": [{"id": "c1", "name": "Camp1"}], "total": 1}),
            )
        )
        async with _make_client() as client:
            data = await client.call_mcp_tool("list_campaigns", {"limit": 10})

    assert data["total"] == 1
    assert data["items"][0]["id"] == "c1"


# Fallback: если structuredContent отсутствует — парсим content[0].text как JSON.
@pytest.mark.asyncio
async def test_call_mcp_tool_falls_back_to_content_text() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": '{"data":[1,2,3]}'}]},
    }
    with respx.mock(assert_all_called=True) as mock:
        mock.post(_MCP_URL).mock(return_value=Response(200, json=payload))
        async with _make_client() as client:
            data = await client.call_mcp_tool("query_stats", {})
    assert data == {"data": [1, 2, 3]}
