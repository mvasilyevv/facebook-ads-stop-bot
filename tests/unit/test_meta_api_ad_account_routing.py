# -*- coding: utf-8 -*-
"""Unit-тесты fail-closed адресации кабинета в ExecuteGraphCallV5."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from clients.python_grpc.v1 import meta_api_pb2
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
    AmbiguousResultError,
    BrowserReadinessRejectedError,
    PermanentError,
    SessionUnavailableError,
)


class _RpcFailure(grpc.RpcError):
    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


def _ok_response() -> meta_api_pb2.ExecuteGraphCallResponse:
    """Успешный Graph-ответ без error-блока (HTTP 200, пустой JSON-объект)."""
    return meta_api_pb2.ExecuteGraphCallResponse(
        status_code=200,
        response_json="{}",
        duration_ms=42,
    )


def _operation_authorization() -> dict[str, object]:
    return {
        "session_id": "sess-1",
        "vision_profile_id": "profile-1",
        "authorized_caller": "meta_api",
        "task_id": 101,
        "lease_owner": "00000000-0000-0000-0000-000000000101",
        "lease_token": 7,
        "capability_expires_at": 2_000_000_000,
        "capability_nonce": "nonce-1",
        "capability_signature": "signature-1",
    }


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
    client.prepare_operation_authorization = AsyncMock(  # type: ignore[method-assign]
        return_value=_operation_authorization()
    )
    return client, breaker_call


def _make_client_for_response(
    response: meta_api_pb2.ExecuteGraphCallResponse,
) -> MetaApiClient:
    breaker = MagicMock()
    breaker.call = AsyncMock(return_value=response)
    client = MetaApiClient(session_id="sess-1", circuit_breaker=breaker)
    client._stub = MagicMock()
    client.prepare_operation_authorization = AsyncMock(  # type: ignore[method-assign]
        return_value=_operation_authorization()
    )
    return client


def _captured_request(breaker_call: AsyncMock) -> meta_api_pb2.ExecuteGraphCallRequest:
    """Извлечь ExecuteGraphCallRequest, переданный в circuit_breaker.call."""
    # call(self._stub.ExecuteGraphCallV5, req, timeout=...) → req — второй позиционный.
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
    # Предохранителю отдаётся обёртка, которая ставит отметку об отправке ровно
    # перед транспортом (отказ предохранителя = отказ ДО отправки). Маршрут при
    # этом прежний: обёрнут именно ExecuteGraphCallV5.
    assert breaker_call.await_args.args[0].__wrapped__ is client._stub.ExecuteGraphCallV5
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


# Account-independent read-only calls remain valid without an account.
@pytest.mark.asyncio
async def test_account_independent_read_allows_no_account() -> None:
    client, breaker_call = _make_client()

    result = await client.execute_graph_call(method="GET", endpoint="/me")

    req = _captured_request(breaker_call)
    assert req.ad_account_id == ""
    # Возврат — распарсенный JSON ответа (контракт не сломан).
    assert result == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "endpoint", "query_params"),
    [
        ("POST", "/act_123/campaigns", None),
        ("DELETE", "/123", None),
        ("GET", "/123", {"fields": "id,status"}),
    ],
)
async def test_money_call_without_explicit_account_is_rejected_before_grpc(
    method: str,
    endpoint: str,
    query_params: dict[str, str] | None,
) -> None:
    client, breaker_call = _make_client()

    with pytest.raises(ValueError, match="requires explicit ad_account_id"):
        await client.execute_graph_call(
            method=method,
            endpoint=endpoint,
            query_params=query_params,
        )

    breaker_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_overrides",
    [
        {"query_params": {"method": "post"}},
        {"query_params": {"MeThOd": "POST"}},
        {"query_params": {"%256dethod": "post"}},
        {"query_params": {"%25252525256dethod": "post"}},
        {"query_params": {"method": "get", "METHOD": "post"}},
        {"endpoint": "/me?method=post"},
        {"endpoint": "/me?method=get&METHOD=post"},
        {"endpoint": "/me%253Fmethod%253Dpost"},
        {"endpoint": "/me%25252525253Fmethod%25252525253Dpost"},
        {"body_json": {"method": "post"}},
        {"body_json": '{"\\u006dethod":"post"}'},
        {"body_json": '{"%256dethod":"post"}'},
        {"body_json": '{"%25252525256dethod":"post"}'},
        {"body_json": '{"method":"GET","method":"POST"}'},
        {"body_json": "method%3Dpost"},
    ],
    ids=[
        "query",
        "query-case",
        "query-double-encoded-key",
        "query-six-layer-key",
        "query-duplicate-case",
        "endpoint-query",
        "endpoint-duplicate",
        "endpoint-double-encoded",
        "endpoint-six-layer-encoded",
        "body-map",
        "body-unicode-key",
        "body-double-encoded-key",
        "body-six-layer-key",
        "body-duplicate",
        "body-urlencoded",
    ],
)
async def test_method_override_is_rejected_before_authority_or_grpc(
    request_overrides: dict[str, object],
) -> None:
    client, breaker_call = _make_client()
    request: dict[str, object] = {
        "method": "GET",
        "endpoint": "/me",
        **request_overrides,
    }

    with pytest.raises(ValueError, match="method|semantics|canonical"):
        await client.execute_graph_call(**request)  # type: ignore[arg-type]

    client.prepare_operation_authorization.assert_not_awaited()  # type: ignore[attr-defined]
    breaker_call.assert_not_awaited()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("account_id", ["42", "act_42"])
async def test_get_ad_insights_uses_graph_form_and_numeric_transport(account_id: str) -> None:
    client, breaker_call = _make_client()

    await client.get_ad_insights(ad_account_id=account_id, fields=["spend"])

    req = _captured_request(breaker_call)
    assert req.endpoint == "/act_42/insights"
    assert req.ad_account_id == "42"


@pytest.mark.asyncio
async def test_invalid_json_response_is_ambiguous_after_dispatch() -> None:
    client = _make_client_for_response(
        meta_api_pb2.ExecuteGraphCallResponse(
            status_code=200,
            response_json="{not-json",
        )
    )

    with pytest.raises(AmbiguousResultError, match="JSON"):
        await client.execute_graph_call(method="POST", endpoint="/123", ad_account_id="123")


@pytest.mark.asyncio
async def test_unstructured_http_error_is_ambiguous_after_dispatch() -> None:
    client = _make_client_for_response(
        meta_api_pb2.ExecuteGraphCallResponse(
            status_code=502,
            response_json="upstream failure",
        )
    )

    with pytest.raises(AmbiguousResultError, match="502") as raised:
        await client.execute_graph_call(method="POST", endpoint="/123", ad_account_id="123")

    assert raised.value.code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "reason_code"),
    [
        (
            grpc.StatusCode.FAILED_PRECONDITION,
            "presend_session_precondition",
        ),
        (
            grpc.StatusCode.UNIMPLEMENTED,
            "presend_contract_unimplemented",
        ),
    ],
)
async def test_controlled_presend_grpc_rejection_closes_claimed_readiness(
    status: grpc.StatusCode,
    reason_code: str,
) -> None:
    breaker = MagicMock()
    breaker.call = AsyncMock(side_effect=_RpcFailure(status, "rejected before browser fetch"))
    client = MetaApiClient(session_id="sess-1", circuit_breaker=breaker)
    client._stub = MagicMock()
    client.prepare_operation_authorization = AsyncMock(  # type: ignore[method-assign]
        return_value=_operation_authorization()
    )
    invalidate = AsyncMock()
    client._invalidate_claimed_browser_readiness = invalidate  # type: ignore[method-assign]

    with client.operation_authority(
        caller="autopause",
        task_id=101,
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        lease_token=7,
        vision_profile_id="profile-1",
        browser_readiness_generation=9,
    ):
        with pytest.raises(
            BrowserReadinessRejectedError,
            match="before Meta dispatch",
        ):
            await client.execute_graph_call(
                method="POST",
                endpoint="/123",
                query_params={"status": "PAUSED"},
                ad_account_id="123",
            )

    invalidate.assert_awaited_once()
    assert invalidate.await_args.kwargs["reason_code"] == reason_code


@pytest.mark.asyncio
async def test_controlled_permission_rejection_keeps_authorization_semantics() -> None:
    breaker = MagicMock()
    breaker.call = AsyncMock(
        side_effect=_RpcFailure(
            grpc.StatusCode.PERMISSION_DENIED,
            "capability binding rejected",
        )
    )
    client = MetaApiClient(session_id="sess-1", circuit_breaker=breaker)
    client._stub = MagicMock()
    client.prepare_operation_authorization = AsyncMock(  # type: ignore[method-assign]
        return_value=_operation_authorization()
    )
    invalidate = AsyncMock()
    client._invalidate_claimed_browser_readiness = invalidate  # type: ignore[method-assign]

    with client.operation_authority(
        caller="autopause",
        task_id=101,
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        lease_token=7,
        vision_profile_id="profile-1",
        browser_readiness_generation=9,
    ):
        with pytest.raises(PermanentError, match="authorization rejected"):
            await client.execute_graph_call(
                method="POST",
                endpoint="/123",
                query_params={"status": "PAUSED"},
                ad_account_id="123",
            )

    invalidate.assert_not_awaited()


def test_failed_precondition_is_the_explicit_presend_grpc_contract() -> None:
    rpc = SimpleNamespace(
        code=lambda: grpc.StatusCode.FAILED_PRECONDITION,
        details=lambda: "token unavailable before Graph fetch",
    )

    mapped = MetaApiClient._grpc_to_meta_error(rpc, endpoint="/123")

    assert isinstance(mapped, SessionUnavailableError)
    assert not isinstance(mapped, AmbiguousResultError)


def test_unimplemented_is_a_presend_browser_contract_mismatch() -> None:
    rpc = SimpleNamespace(
        code=lambda: grpc.StatusCode.UNIMPLEMENTED,
        details=lambda: "Method not found",
    )

    mapped = MetaApiClient._grpc_to_meta_error(rpc, endpoint="/123")

    assert isinstance(mapped, SessionUnavailableError)
    assert not isinstance(mapped, AmbiguousResultError)
    assert "contract is incompatible" in str(mapped)


def test_python_descriptor_exposes_only_the_v5_graph_rpc() -> None:
    service = meta_api_pb2.DESCRIPTOR.services_by_name["MetaApiService"]

    assert "ExecuteGraphCallV5" in service.methods_by_name
    assert "ExecuteGraphCall" not in service.methods_by_name


@pytest.mark.parametrize(
    "status",
    [
        grpc.StatusCode.ABORTED,
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.CANCELLED,
        grpc.StatusCode.INTERNAL,
    ],
)
def test_grpc_failure_after_dispatch_is_ambiguous(status: grpc.StatusCode) -> None:
    rpc = SimpleNamespace(code=lambda: status, details=lambda: "response lost")

    mapped = MetaApiClient._grpc_to_meta_error(rpc, endpoint="/123")

    assert isinstance(mapped, AmbiguousResultError)
