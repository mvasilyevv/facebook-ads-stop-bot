# -*- coding: utf-8 -*-
"""Unit-тесты явной адресации кабинета в ExecuteGraphCall.

Мульти-кабинет: client пробрасывает ad_account_id в gRPC-request, чтобы залив
адресовал конкретный act-кабинет (вкладка своего кабинета), а не «активную
вкладку Vision». Обратная совместимость: None/"" → пустой ad_account_id, старый
путь не ломается.

Покрываем:
- execute_graph_call с ad_account_id → request.ad_account_id == числовой ID.
- ad_account_id с префиксом "act_" → префикс снимается (browser-agent ждёт числовой).
- ad_account_id=None → request.ad_account_id == "" (legacy primary-вкладка).
- ad_account_id не передан → дефолт "" (обратная совместимость сигнатуры).
- ad_account_id прокидывается одновременно с остальными полями (method/endpoint).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.python_grpc.v1 import meta_api_pb2
from core.meta_api.client import MetaApiClient


def _ok_response() -> meta_api_pb2.ExecuteGraphCallResponse:
    """Успешный Graph-ответ без error-блока (HTTP 200, пустой JSON-объект)."""
    return meta_api_pb2.ExecuteGraphCallResponse(
        status_code=200,
        response_json="{}",
        duration_ms=42,
    )


def _make_client() -> tuple[MetaApiClient, AsyncMock]:
    """MetaApiClient с замоканным stub и circuit_breaker.

    circuit_breaker.call(fn, req, timeout=...) — возвращает _ok_response, но
    реальный gRPC не дёргается. Захватываем req через breaker_call.call_args.
    """
    breaker = MagicMock()
    breaker_call = AsyncMock(return_value=_ok_response())
    breaker.call = breaker_call

    client = MetaApiClient(session_id="sess-1", circuit_breaker=breaker)
    # _stub любой не-None: его метод передаётся в breaker.call как callable,
    # но сам не вызывается (breaker замокан).
    client._stub = MagicMock()
    return client, breaker_call


def _captured_request(breaker_call: AsyncMock) -> meta_api_pb2.ExecuteGraphCallRequest:
    """Извлечь ExecuteGraphCallRequest, переданный в circuit_breaker.call."""
    # call(self._stub.ExecuteGraphCall, req, timeout=...) → req — второй позиционный.
    return breaker_call.call_args.args[1]


# ad_account_id передан → request.ad_account_id == числовой ID.
@pytest.mark.asyncio
async def test_execute_graph_call_passes_ad_account_id() -> None:
    client, breaker_call = _make_client()

    await client.execute_graph_call(
        method="POST",
        endpoint="/act_555/campaigns",
        ad_account_id="555",
    )

    req = _captured_request(breaker_call)
    assert req.ad_account_id == "555"


# ad_account_id с префиксом "act_" → префикс снимается (browser-agent ждёт числовой).
@pytest.mark.asyncio
async def test_execute_graph_call_strips_act_prefix() -> None:
    client, breaker_call = _make_client()

    await client.execute_graph_call(
        method="GET",
        endpoint="/me",
        ad_account_id="act_987654321",
    )

    req = _captured_request(breaker_call)
    assert req.ad_account_id == "987654321"


# ad_account_id=None → пустой ad_account_id (legacy primary-вкладка, обратная совместимость).
@pytest.mark.asyncio
async def test_execute_graph_call_none_uses_empty() -> None:
    client, breaker_call = _make_client()

    await client.execute_graph_call(
        method="GET",
        endpoint="/me",
        ad_account_id=None,
    )

    req = _captured_request(breaker_call)
    assert req.ad_account_id == ""


# ad_account_id не передан вовсе → дефолт "" (старый код без аргумента не ломается).
@pytest.mark.asyncio
async def test_execute_graph_call_default_no_account() -> None:
    client, breaker_call = _make_client()

    result = await client.execute_graph_call(method="GET", endpoint="/me")

    req = _captured_request(breaker_call)
    assert req.ad_account_id == ""
    # Возврат — распарсенный JSON ответа (контракт не сломан).
    assert result == {}


# ad_account_id прокидывается рядом с остальными полями (method/endpoint не теряются).
@pytest.mark.asyncio
async def test_execute_graph_call_account_alongside_other_fields() -> None:
    client, breaker_call = _make_client()

    await client.execute_graph_call(
        method="post",
        endpoint="/act_42/adsets",
        query_params={"limit": "10"},
        ad_account_id="act_42",
    )

    req = _captured_request(breaker_call)
    assert req.ad_account_id == "42"
    assert req.method == "POST"
    assert req.endpoint == "/act_42/adsets"
    assert req.query_params["limit"] == "10"
    assert req.session_id == "sess-1"
