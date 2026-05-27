# -*- coding: utf-8 -*-
"""Unit-тесты CustomAudienceHandler — все три действия (create/update/delete)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.meta_api.mutations.custom_audience import CustomAudienceHandler
from core.meta_api.schemas import MetaMutationPayload


def _make_client(response: Any = None) -> AsyncMock:
    """Фабрика мок-клиента: execute_graph_call → заданный response."""
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value=response or {"id": "23840000001"})
    return client


# ====================== create CUSTOM ======================


# create CUSTOM с минимальными полями → POST /act_42/customaudiences, subtype и name в body.
@pytest.mark.asyncio
async def test_create_custom_minimal_fields() -> None:
    client = _make_client({"id": "23840000001"})
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        ad_account_id="act_42",
        params={
            "action": "create",
            "name": "Buyers Jan",
            "subtype": "CUSTOM",
        },
    )

    result = await CustomAudienceHandler().execute(client, payload)

    client.execute_graph_call.assert_awaited_once()
    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["endpoint"] == "/act_42/customaudiences"
    body = kwargs["body_json"]
    assert body["name"] == "Buyers Jan"
    assert body["subtype"] == "CUSTOM"
    assert result["success"] is True
    assert "23840000001" in result["modified_ids"]
    assert result["action"] == "create"


# create CUSTOM с customer_file_source → поле попадает в body.
@pytest.mark.asyncio
async def test_create_custom_with_customer_file_source() -> None:
    client = _make_client({"id": "23840000002"})
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        ad_account_id="act_42",
        params={
            "action": "create",
            "name": "Email List",
            "subtype": "CUSTOM",
            "customer_file_source": "USER_PROVIDED_ONLY",
            "description": "Список email-покупателей",
        },
    )

    await CustomAudienceHandler().execute(client, payload)

    body = client.execute_graph_call.call_args.kwargs["body_json"]
    assert body["customer_file_source"] == "USER_PROVIDED_ONLY"
    assert body["description"] == "Список email-покупателей"


# ====================== create LOOKALIKE ======================


# create LOOKALIKE → endpoint тот же, body содержит lookalike_spec как JSON-строку.
@pytest.mark.asyncio
async def test_create_lookalike_sends_lookalike_spec() -> None:
    client = _make_client({"id": "23840000010"})
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        ad_account_id="act_42",
        params={
            "action": "create",
            "name": "LAL Buyers 1%",
            "subtype": "LOOKALIKE",
            "origin_audience_id": "23840000001",
            "country": "US",
            "ratio": 0.01,
        },
    )

    result = await CustomAudienceHandler().execute(client, payload)

    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["endpoint"] == "/act_42/customaudiences"
    body = kwargs["body_json"]
    assert body["subtype"] == "LOOKALIKE"
    # lookalike_spec должна быть JSON-строкой с ожидаемыми ключами.
    spec = json.loads(body["lookalike_spec"])
    assert spec["origin_audience_id"] == "23840000001"
    assert spec["country"] == "US"
    assert spec["ratio"] == 0.01
    assert spec["type"] == "similarity"
    assert result["success"] is True


# LOOKALIKE с ratio=0.20 (максимальный) — успешно.
@pytest.mark.asyncio
async def test_create_lookalike_max_ratio_allowed() -> None:
    client = _make_client({"id": "23840000011"})
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        ad_account_id="act_42",
        params={
            "action": "create",
            "name": "LAL 20%",
            "subtype": "LOOKALIKE",
            "origin_audience_id": "23840000001",
            "country": "DE",
            "ratio": 0.20,
        },
    )

    await CustomAudienceHandler().execute(client, payload)

    body = client.execute_graph_call.call_args.kwargs["body_json"]
    spec = json.loads(body["lookalike_spec"])
    assert spec["ratio"] == 0.20
    assert spec["country"] == "DE"


# ====================== update ======================


# update name → POST /{audience_id} с body={name: ...}.
@pytest.mark.asyncio
async def test_update_name_sends_post_with_body() -> None:
    client = _make_client({"success": True})
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="23000000",
        params={"action": "update", "name": "Обновлённая аудитория"},
    )

    result = await CustomAudienceHandler().execute(client, payload)

    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["endpoint"] == "/23000000"
    assert kwargs["body_json"] == {"name": "Обновлённая аудитория"}
    assert result["action"] == "update"
    assert "23000000" in result["modified_ids"]


# update description → body содержит только description.
@pytest.mark.asyncio
async def test_update_description_only() -> None:
    client = _make_client({"success": True})
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="23000001",
        params={"action": "update", "description": "Новое описание"},
    )

    await CustomAudienceHandler().execute(client, payload)

    body = client.execute_graph_call.call_args.kwargs["body_json"]
    assert "description" in body
    assert "name" not in body


# update без полей для обновления → ValueError.
@pytest.mark.asyncio
async def test_update_without_fields_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="23000000",
        params={"action": "update"},
    )

    with pytest.raises(ValueError, match="хотя бы одно поле"):
        await CustomAudienceHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# ====================== delete ======================


# delete → DELETE /{audience_id}.
@pytest.mark.asyncio
async def test_delete_sends_delete_method() -> None:
    client = _make_client({"success": True})
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="23000000",
        params={"action": "delete"},
    )

    result = await CustomAudienceHandler().execute(client, payload)

    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["method"] == "DELETE"
    assert kwargs["endpoint"] == "/23000000"
    assert result["action"] == "delete"
    assert "23000000" in result["modified_ids"]


# ====================== валидация ======================


# action="invalid" → ValueError.
@pytest.mark.asyncio
async def test_invalid_action_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        params={"action": "invalid"},
    )

    with pytest.raises(ValueError, match="action должен быть"):
        await CustomAudienceHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# create без name → ValueError.
@pytest.mark.asyncio
async def test_create_without_name_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        ad_account_id="act_42",
        params={"action": "create", "subtype": "CUSTOM"},
    )

    with pytest.raises(ValueError, match="name обязателен"):
        await CustomAudienceHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# create без ad_account_id → ValueError.
@pytest.mark.asyncio
async def test_create_without_ad_account_id_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        params={"action": "create", "name": "Test", "subtype": "CUSTOM"},
    )

    with pytest.raises(ValueError, match="ad_account_id обязателен"):
        await CustomAudienceHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# LOOKALIKE без origin_audience_id → ValueError.
@pytest.mark.asyncio
async def test_create_lookalike_without_origin_audience_id_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        ad_account_id="act_42",
        params={
            "action": "create",
            "name": "LAL",
            "subtype": "LOOKALIKE",
            "country": "US",
            "ratio": 0.01,
        },
    )

    with pytest.raises(ValueError, match="origin_audience_id обязателен"):
        await CustomAudienceHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# LOOKALIKE с ratio=0.5 (слишком много) → ValueError.
@pytest.mark.asyncio
async def test_create_lookalike_ratio_too_large_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        ad_account_id="act_42",
        params={
            "action": "create",
            "name": "LAL 50%",
            "subtype": "LOOKALIKE",
            "origin_audience_id": "23840000001",
            "country": "US",
            "ratio": 0.5,
        },
    )

    with pytest.raises(ValueError, match="ratio должен быть в диапазоне"):
        await CustomAudienceHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# LOOKALIKE с ratio=0.0 (ниже минимума) → ValueError.
@pytest.mark.asyncio
async def test_create_lookalike_ratio_too_small_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        ad_account_id="act_42",
        params={
            "action": "create",
            "name": "LAL 0%",
            "subtype": "LOOKALIKE",
            "origin_audience_id": "23840000001",
            "country": "US",
            "ratio": 0.0,
        },
    )

    with pytest.raises(ValueError, match="ratio должен быть в диапазоне"):
        await CustomAudienceHandler().execute(client, payload)


# delete с нечисловым audience_id → ValueError.
@pytest.mark.asyncio
async def test_delete_non_numeric_target_id_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="act_not_a_number",
        params={"action": "delete"},
    )

    with pytest.raises(ValueError, match="только из цифр"):
        await CustomAudienceHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()
