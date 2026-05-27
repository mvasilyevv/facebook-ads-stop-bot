# -*- coding: utf-8 -*-
"""Unit-тесты SetAdCreativeHandler — замена creative у объявления."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.meta_api.mutations.set_ad_creative import SetAdCreativeHandler
from core.meta_api.schemas import MetaMutationPayload


def _make_client(response: Any = None) -> AsyncMock:
    """Фабрика мок-клиента: execute_graph_call → заданный response."""
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value=response or {"success": True})
    return client


# ====================== success ======================


# Успешный вызов: POST /{ad_id} с body={"creative": {"creative_id": "..."}}.
@pytest.mark.asyncio
async def test_set_ad_creative_success_sends_correct_request() -> None:
    client = _make_client({"success": True})
    payload = MetaMutationPayload(
        mutation_kind="set_ad_creative",
        target_id="23847000001",
        params={"creative_id": "23848000001"},
        ad_account_id="act_42",
    )

    result = await SetAdCreativeHandler().execute(client, payload)

    client.execute_graph_call.assert_awaited_once()
    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["endpoint"] == "/23847000001"
    # Meta ожидает вложенный объект creative, не плоский параметр.
    assert kwargs["body_json"] == {"creative": {"creative_id": "23848000001"}}
    assert result["success"] is True
    assert "23847000001" in result["modified_ids"]
    assert result["creative_id"] == "23848000001"


# Ответ Meta сохраняется в graph_response.
@pytest.mark.asyncio
async def test_set_ad_creative_graph_response_preserved() -> None:
    meta_resp = {"id": "23847000001", "creative": {"id": "23848000001"}}
    client = _make_client(meta_resp)
    payload = MetaMutationPayload(
        mutation_kind="set_ad_creative",
        target_id="23847000001",
        params={"creative_id": "23848000001"},
    )

    result = await SetAdCreativeHandler().execute(client, payload)

    assert result["graph_response"] == meta_resp


# ====================== валидация ======================


# ad_id нечисловой → ValueError до вызова API.
@pytest.mark.asyncio
async def test_set_ad_creative_non_numeric_ad_id_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_ad_creative",
        target_id="abc_not_numeric",
        params={"creative_id": "23848000001"},
    )

    with pytest.raises(ValueError, match="только из цифр"):
        await SetAdCreativeHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# creative_id отсутствует в params → ValueError.
@pytest.mark.asyncio
async def test_set_ad_creative_missing_creative_id_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_ad_creative",
        target_id="23847000001",
        params={},
    )

    with pytest.raises(ValueError, match="creative_id обязателен"):
        await SetAdCreativeHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# creative_id нечисловой → ValueError.
@pytest.mark.asyncio
async def test_set_ad_creative_non_numeric_creative_id_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_ad_creative",
        target_id="23847000001",
        params={"creative_id": "not_a_number"},
    )

    with pytest.raises(ValueError, match="только из цифр"):
        await SetAdCreativeHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# Пустой creative_id → ValueError.
@pytest.mark.asyncio
async def test_set_ad_creative_empty_creative_id_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_ad_creative",
        target_id="23847000001",
        params={"creative_id": ""},
    )

    with pytest.raises(ValueError, match="creative_id обязателен"):
        await SetAdCreativeHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()
