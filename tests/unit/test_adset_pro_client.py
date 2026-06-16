# -*- coding: utf-8 -*-
"""Unit-тесты core.adset_pro.client — pure-функции и классификация HTTP-ошибок.

HTTP-уровень покрыт интеграционно через respx (tests/integration/test_adset_pro_client_http.py).
Здесь — изолированно от сети, проверяем pure-helpers MCP-обвязки.
"""

from __future__ import annotations

from datetime import date

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
from core.adset_pro.schemas import StatsQueryRequest


# Заголовок Authorization формируется по схеме Bearer (текущий контракт MCP-key).
def test_build_headers_uses_bearer_scheme() -> None:
    headers = _build_headers("mcp_test_key")
    assert headers["Authorization"] == "Bearer mcp_test_key"
    # Accept должен поддерживать и JSON и SSE — MCP-серверы иногда стримят.
    assert "application/json" in headers["Accept"]
    assert "text/event-stream" in headers["Accept"]
    assert headers["Content-Type"] == "application/json"


# 401/403 → AuthError (постоянная — не ретраим).
@pytest.mark.parametrize("status", [401, 403])
def test_classify_http_error_auth(status: int) -> None:
    exc = classify_http_error(status, "Unauthorized", endpoint="/mcp")
    assert isinstance(exc, AuthError)
    assert isinstance(exc, PermanentError)
    assert exc.status_code == status
    assert exc.endpoint == "/mcp"


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


# JSON-RPC envelope содержит обязательные поля jsonrpc/method/id, params опционально.
def test_make_rpc_envelope_basic_shape() -> None:
    client = AdsetProClient(api_key="x", base_url="https://x.test")
    env = client._make_rpc_envelope(method="initialize")
    assert env["jsonrpc"] == "2.0"
    assert env["method"] == "initialize"
    assert isinstance(env["id"], int)
    assert "params" not in env


# Если params переданы — попадают в envelope. id монотонно увеличивается.
def test_make_rpc_envelope_includes_params_and_increments_id() -> None:
    client = AdsetProClient(api_key="x", base_url="https://x.test")
    e1 = client._make_rpc_envelope(method="tools/call", params={"name": "x"})
    e2 = client._make_rpc_envelope(method="tools/call", params={"name": "y"})
    assert e1["params"] == {"name": "x"}
    assert e2["id"] == e1["id"] + 1


# StatsQueryRequest без ad_id → MCP arguments только с from/to.
def test_stats_args_from_request_minimal() -> None:
    args = AdsetProClient._stats_args_from_request(
        StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 10))
    )
    assert args == {"from": "2026-05-01", "to": "2026-05-10"}


# С ad_id → добавляется filter ext_sub6=eq.
def test_stats_args_from_request_with_ad_id() -> None:
    args = AdsetProClient._stats_args_from_request(
        StatsQueryRequest(
            since=date(2026, 5, 1),
            until=date(2026, 5, 10),
            ad_id="23000000111",
        )
    )
    assert args["filters"] == [{"field": "ext_sub6", "op": "eq", "value": "23000000111"}]


# extra_filters в форме {field,op,value} пробрасывается как есть; group_by → groups.
def test_stats_args_from_request_extra_filters_and_groups() -> None:
    args = AdsetProClient._stats_args_from_request(
        StatsQueryRequest(
            since=date(2026, 5, 1),
            until=date(2026, 5, 10),
            group_by=("country", "cmp_campaign"),
            extra_filters={"country": {"field": "country", "op": "in", "value": "DE,FR"}},
        )
    )
    assert args["groups"] == ["country", "cmp_campaign"]
    assert {"field": "country", "op": "in", "value": "DE,FR"} in args["filters"]


# extra_filters в форме {key:value} (короткая) → переводится в равенство (eq).
def test_stats_args_from_request_short_extra_filter_form() -> None:
    args = AdsetProClient._stats_args_from_request(
        StatsQueryRequest(
            since=date(2026, 5, 1),
            until=date(2026, 5, 10),
            extra_filters={"event_type": "ftd"},
        )
    )
    assert {"field": "event_type", "op": "eq", "value": "ftd"} in args["filters"]


# _extract_tool_result отдаёт structuredContent, если он dict.
def test_extract_tool_result_structured_dict() -> None:
    rpc = {"result": {"structuredContent": {"data": [1, 2]}}}
    out = AdsetProClient._extract_tool_result(rpc, tool_name="x")
    assert out == {"data": [1, 2]}


# Fallback: если structuredContent отсутствует, парсим content[0].text как JSON.
def test_extract_tool_result_falls_back_to_text_json() -> None:
    rpc = {"result": {"content": [{"type": "text", "text": '{"foo": "bar"}'}]}}
    out = AdsetProClient._extract_tool_result(rpc, tool_name="x")
    assert out == {"foo": "bar"}


# JSON-массив в text оборачивается в {"data": [...]} — гарантия dict-shape для потребителей.
def test_extract_tool_result_text_json_array_wrapped() -> None:
    rpc = {"result": {"content": [{"type": "text", "text": "[1, 2, 3]"}]}}
    out = AdsetProClient._extract_tool_result(rpc, tool_name="x")
    assert out == {"data": [1, 2, 3]}


# Если result отсутствует или пуст — возвращаем {} (не None).
def test_extract_tool_result_empty_payload() -> None:
    assert AdsetProClient._extract_tool_result({}, tool_name="x") == {}
    assert AdsetProClient._extract_tool_result({"result": {}}, tool_name="x") == {}


# isError + текст про scope/auth → AuthError (write-фейл read-only ключа не теряется).
def test_raise_if_tool_error_scope_is_auth() -> None:
    resp = {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": "Error: MCP API key not authenticated or missing required scope",
                }
            ],
            "isError": True,
        }
    }
    with pytest.raises(AuthError):
        AdsetProClient._raise_if_tool_error(resp, tool_name="create_pwa")


# isError с прочим текстом → общий AdsetProError.
def test_raise_if_tool_error_generic() -> None:
    resp = {
        "result": {
            "content": [{"type": "text", "text": "Validation failed: name required"}],
            "isError": True,
        }
    }
    with pytest.raises(AdsetProError):
        AdsetProClient._raise_if_tool_error(resp, tool_name="create_offer")


# Нет isError → не бросает (обычный успех).
def test_raise_if_tool_error_ok() -> None:
    AdsetProClient._raise_if_tool_error(
        {"result": {"structuredContent": {"id": "x"}}}, tool_name="x"
    )
    AdsetProClient._raise_if_tool_error({}, tool_name="x")
