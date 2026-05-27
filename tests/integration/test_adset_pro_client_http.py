# -*- coding: utf-8 -*-
"""Интеграционный: AdsetProClient → реальный HTTP через httpx + respx (без живого AdSet.pro).

Проверяет правильность сериализации запроса (заголовки, путь, payload) и
корректную обработку HTTP-статусов. Без этого слоя поломки на стыке
core.adset_pro.client ↔ AdSet.pro REST не ловились бы до прод-инцидента.

Прод-API сейчас не дёргаем — только моки (см. CLAUDE.md, Этап 6 META_INTEGRATION_PLAN).
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

_BASE_URL = "https://api.adset.pro.test"
_HEALTH_URL = f"{_BASE_URL}/ping"
_STATS_URL = f"{_BASE_URL}/api/stats/query"


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


# Сценарий: /ping отвечает 200 → health_check возвращает True.
@pytest.mark.asyncio
async def test_health_check_returns_true_on_200() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_HEALTH_URL).mock(return_value=Response(200, json={"ok": True}))
        async with _make_client() as client:
            assert await client.health_check() is True


# Сценарий: /ping отвечает 503 → health_check возвращает False (не бросает).
@pytest.mark.asyncio
async def test_health_check_returns_false_on_5xx() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_HEALTH_URL).mock(return_value=Response(503, text="down"))
        async with _make_client() as client:
            assert await client.health_check() is False


# Сценарий: /ping роняет сетевую ошибку → health_check возвращает False.
@pytest.mark.asyncio
async def test_health_check_returns_false_on_network_error() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_HEALTH_URL).mock(side_effect=TransportError("dns fail"))
        async with _make_client() as client:
            assert await client.health_check() is False


# Сценарий: query_stats отправляет POST с правильным Authorization и payload.
@pytest.mark.asyncio
async def test_query_stats_sends_bearer_token_and_payload() -> None:
    captured: dict = {}

    def _handler(request):
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content) if request.content else {}
        return Response(
            200,
            json={
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
            },
        )

    with respx.mock(assert_all_called=True) as mock:
        mock.post(_STATS_URL).mock(side_effect=_handler)
        async with _make_client() as client:
            resp = await client.query_stats(
                StatsQueryRequest(
                    since=date(2026, 5, 1),
                    until=date(2026, 5, 15),
                    ad_id="ad-1",
                )
            )

    # Авторизация Bearer.
    assert captured["headers"].get("authorization") == "Bearer mcp_test_key"
    # Payload содержит границы интервала и фильтр по ext_sub6.
    assert captured["body"]["since"] == "2026-05-01"
    assert captured["body"]["until"] == "2026-05-15"
    assert captured["body"]["filters"]["ext_sub6"] == "ad-1"

    # Ответ распарсился корректно.
    assert len(resp.rows) == 1
    row = resp.rows[0]
    assert row.click_id == "c1"
    assert row.fb_ad_id == "ad-1"
    assert row.event_type == "ftd"
    assert row.revenue == Decimal("10.50")
    assert row.currency == "EUR"


# Сценарий: AdSet.pro отвечает 401 → AuthError, и retry НЕ происходит (permanent).
@pytest.mark.asyncio
async def test_query_stats_401_raises_auth_error_without_retry() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(_STATS_URL).mock(
            return_value=Response(401, json={"error": "Invalid key"})
        )
        async with _make_client() as client:
            with pytest.raises(AuthError) as info:
                await client.query_stats(
                    StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 2))
                )
    assert info.value.status_code == 401
    # Permanent ошибки не ретраятся — должен быть ровно один вызов.
    assert route.call_count == 1


# Сценарий: 404 → NotFoundError (тоже без retry).
@pytest.mark.asyncio
async def test_query_stats_404_raises_not_found() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_STATS_URL).mock(return_value=Response(404, text="no such endpoint"))
        async with _make_client() as client:
            with pytest.raises(NotFoundError):
                await client.query_stats(
                    StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 2))
                )


# Сценарий: 429 → RateLimitedError; ретраи доходят до max_retries и пробрасывают исходное.
@pytest.mark.asyncio
async def test_query_stats_429_retries_then_raises() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(_STATS_URL).mock(return_value=Response(429, text="slow down"))
        async with _make_client(max_retries=2) as client:
            with pytest.raises(RateLimitedError):
                await client.query_stats(
                    StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 2))
                )
    # Ровно max_retries попыток — backoff внутри tenacity (с min=1.0 это даст ~1s, держим тест в timeout=5).
    assert route.call_count == 2


# Сценарий: 5xx один раз → 200 на второй попытке → клиент возвращает успешный ответ.
@pytest.mark.asyncio
async def test_query_stats_retries_5xx_then_succeeds() -> None:
    responses = [
        Response(500, text="boom"),
        Response(200, json={"data": []}),
    ]

    def _handler(request):
        return responses.pop(0)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(_STATS_URL).mock(side_effect=_handler)
        async with _make_client(max_retries=3) as client:
            resp = await client.query_stats(
                StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 2))
            )

    assert route.call_count == 2
    assert resp.rows == ()


# Сценарий: list_conversions — сахар поверх query_stats, возвращает list[ConversionRow].
@pytest.mark.asyncio
async def test_list_conversions_returns_parsed_rows() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.post(_STATS_URL).mock(
            return_value=Response(
                200,
                json={
                    "rows": [
                        {"click_id": "1", "ext_sub6": "ad-1", "revenue": "5"},
                        {"click_id": "2", "ext_sub6": "ad-1", "revenue": "7.5"},
                    ]
                },
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


# Сценарий: 2xx с битым JSON → TemporaryError (после ретраев) — защита от half-baked ответа.
@pytest.mark.asyncio
async def test_query_stats_invalid_json_raises_temporary() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_STATS_URL).mock(return_value=Response(200, content=b"<<<broken>>>"))
        async with _make_client(max_retries=2) as client:
            with pytest.raises(TemporaryError):
                await client.query_stats(
                    StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 2))
                )
