# -*- coding: utf-8 -*-
"""Unit-тесты core.adset_pro.client — pure-функции и классификация HTTP-ошибок.

HTTP-уровень покрыт интеграционно через respx (tests/integration/test_adset_pro_client_http.py).
Здесь — изолированно от сети.
"""

from __future__ import annotations

import httpx
import pytest

from core.adset_pro.client import AdsetProClient, _build_headers
from core.adset_pro.errors import (
    AdsetProError,
    AuthError,
    NotFoundError,
    PermanentError,
    RateLimitedError,
    TemporaryError,
    classify_http_error,
)


# Заголовок Authorization формируется по схеме Bearer (текущий контракт MCP-key).
def test_build_headers_uses_bearer_scheme() -> None:
    headers = _build_headers("mcp_test_key")
    assert headers["Authorization"] == "Bearer mcp_test_key"
    assert headers["Accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"


# 401/403 → AuthError (постоянная — не ретраим).
@pytest.mark.parametrize("status", [401, 403])
def test_classify_http_error_auth(status: int) -> None:
    exc = classify_http_error(status, "Unauthorized", endpoint="/api/stats/query")
    assert isinstance(exc, AuthError)
    assert isinstance(exc, PermanentError)
    assert exc.status_code == status
    assert exc.endpoint == "/api/stats/query"


# 404 → NotFoundError.
def test_classify_http_error_not_found() -> None:
    exc = classify_http_error(404, "no such conversion")
    assert isinstance(exc, NotFoundError)


# 429 → RateLimitedError (временная — ретраим с backoff).
def test_classify_http_error_rate_limited() -> None:
    exc = classify_http_error(429, "throttled")
    assert isinstance(exc, RateLimitedError)
    assert isinstance(exc, TemporaryError)


# 5xx → TemporaryError (ретраим).
@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_classify_http_error_server_5xx_temporary(status: int) -> None:
    exc = classify_http_error(status, "boom")
    assert isinstance(exc, TemporaryError)


# Неклассифицируемая 4xx (например 418) → PermanentError, не ретраим.
def test_classify_http_error_unknown_4xx_permanent() -> None:
    exc = classify_http_error(418, "I'm a teapot")
    assert isinstance(exc, PermanentError)
    assert not isinstance(exc, TemporaryError)


# _parse_response на 2xx с JSON-объектом возвращает dict как есть.
def test_parse_response_2xx_dict() -> None:
    resp = httpx.Response(200, json={"data": [1, 2, 3], "total": 3})
    out = AdsetProClient._parse_response(resp, endpoint="/x")
    assert out == {"data": [1, 2, 3], "total": 3}


# _parse_response на 2xx с JSON-массивом верхнего уровня оборачивает в {"data": [...]}.
def test_parse_response_2xx_array_wrapped() -> None:
    resp = httpx.Response(200, json=[{"a": 1}, {"a": 2}])
    out = AdsetProClient._parse_response(resp, endpoint="/x")
    assert out == {"data": [{"a": 1}, {"a": 2}]}


# Пустой body на 2xx — возвращаем пустой dict (а не падаем на json()).
def test_parse_response_2xx_empty_body() -> None:
    resp = httpx.Response(204, content=b"")
    out = AdsetProClient._parse_response(resp, endpoint="/x")
    assert out == {}


# 2xx с невалидным JSON → TemporaryError (могла быть половинчатая отдача — retry разумен).
def test_parse_response_2xx_invalid_json_raises_temporary() -> None:
    resp = httpx.Response(200, content=b"<<<not-json>>>")
    with pytest.raises(TemporaryError):
        AdsetProClient._parse_response(resp, endpoint="/x")


# Не-2xx статусы конвертируются в подходящий AdsetProError с сохранением endpoint.
def test_parse_response_5xx_raises_temporary_with_endpoint() -> None:
    resp = httpx.Response(503, text="upstream gone")
    with pytest.raises(TemporaryError) as info:
        AdsetProClient._parse_response(resp, endpoint="/api/stats/query")
    assert isinstance(info.value, AdsetProError)
    assert info.value.endpoint == "/api/stats/query"
    assert info.value.status_code == 503
