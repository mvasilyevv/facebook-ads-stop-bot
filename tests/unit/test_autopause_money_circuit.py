# -*- coding: utf-8 -*-
"""Авто-стоп принадлежит money-контуру по контракту caller, а не по совпадению полей.

Каждый вызов execute_graph_call авто-стопа обязан получать controlled_call=True
через authority.caller == "autopause", независимо от того, что возвращает
_is_money_graph_call(method, endpoint, fields).

Это фиксирует: появление нового вызова авто-стопа, не попадающего под общий
признак по методу/полям, не выведет операцию из money-контура.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.python_grpc.v1 import meta_api_pb2
from core.meta_api.client import MetaApiClient

# ── Полный реестр execute_graph_call, которые авто-стоп делает при
#    исполнении задачи pause_ad и при её reconciliation.
#
#    pause_ad.py → PauseAdHandler.execute
#    apps/meta_api_worker/main.py → _reconcile_unknown_status_action
_AUTOPAUSE_GRAPH_CALLS: list[dict] = [
    {
        "label": "pause_ad — основная мутация",
        "method": "POST",
        "endpoint": "/123456789",
        "query_params": {"status": "PAUSED"},
        "ad_account_id": "123",
    },
    {
        "label": "reconciliation pause_ad — чтение статуса после UNKNOWN",
        "method": "GET",
        "endpoint": "/123456789",
        "query_params": {"fields": "effective_status,status"},
        "ad_account_id": "123",
    },
]


def _ok_response() -> meta_api_pb2.ExecuteGraphCallResponse:
    return meta_api_pb2.ExecuteGraphCallResponse(
        status_code=200,
        response_json="{}",
        duration_ms=1,
    )


def _operation_authorization() -> dict:
    return {
        "session_id": "sess-1",
        "vision_profile_id": "profile-1",
        "authorized_caller": "autopause",
        "task_id": 1,
        "lease_owner": "00000000-0000-0000-0000-000000000001",
        "lease_token": 1,
        "capability_expires_at": 2_000_000_000,
        "capability_nonce": "n" * 32,
        "capability_signature": "sig-1",
    }


def _make_client() -> tuple[MetaApiClient, AsyncMock]:
    breaker = MagicMock()
    breaker.call = AsyncMock(return_value=_ok_response())
    client = MetaApiClient(session_id="sess-1", circuit_breaker=breaker)
    client._stub = MagicMock()
    prepare = AsyncMock(return_value=_operation_authorization())
    client.prepare_operation_authorization = prepare  # type: ignore[method-assign]
    return client, prepare


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call_kwargs",
    [
        pytest.param(
            {k: v for k, v in entry.items() if k != "label"},
            id=entry["label"],
        )
        for entry in _AUTOPAUSE_GRAPH_CALLS
    ],
)
async def test_autopause_graph_call_is_controlled_by_caller_not_by_fields(
    call_kwargs: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """controlled_call должен исходить из caller="autopause", а не из метода/полей.

    _is_money_graph_call возвращает False → controlled_call держится только на
    authority.caller. До правки: "autopause" нет в множестве → prepare не вызывается.
    """
    monkeypatch.setattr(
        "core.meta_api.client._is_money_graph_call",
        lambda **_: False,
    )
    client, prepare = _make_client()

    with client.operation_authority(
        caller="autopause",
        task_id=1,
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        lease_token=1,
        vision_profile_id="profile-1",
    ):
        await client.execute_graph_call(**call_kwargs)

    assert prepare.await_count == 1, (
        f"autopause call «{call_kwargs}» не попал в money-контур: "
        "prepare_operation_authorization не вызван. "
        "Добавь 'autopause' в controlled_call в core/meta_api/client.py."
    )
