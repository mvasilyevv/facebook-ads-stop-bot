# -*- coding: utf-8 -*-
"""Unit-тесты Meta API mutation handlers — без сети, через мок execute_graph_call.

Каждый тест: создать payload → вызвать handler.execute(client_mock, payload) →
проверить что:
1. Правильный method/endpoint/query_params поданы в client.execute_graph_call.
2. Правильная success-форма возвращена.
3. Валидация плохого payload бросает ValueError.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.meta_api.mutations.activate_ad import ActivateAdHandler
from core.meta_api.mutations.activate_campaign import ActivateCampaignHandler
from core.meta_api.mutations.bulk_status_change import BulkStatusChangeHandler
from core.meta_api.mutations.create_campaign import CreateCampaignHandler
from core.meta_api.mutations.duplicate_campaign import DuplicateCampaignHandler
from core.meta_api.mutations.pause_ad import PauseAdHandler
from core.meta_api.mutations.pause_campaign import PauseCampaignHandler
from core.meta_api.mutations.set_adset_budget import SetAdsetBudgetHandler
from core.meta_api.schemas import MetaMutationPayload


def _make_client(response: Any = None) -> AsyncMock:
    """Фабрика мок-клиента: execute_graph_call → заданный response."""
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value=response or {"success": True})
    return client


# ====================== pause_ad / activate_ad ======================


# pause_ad шлёт POST /{ad_id} с status=PAUSED.
@pytest.mark.asyncio
async def test_pause_ad_sends_paused_status() -> None:
    client = _make_client({"success": True})
    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="23847000123")

    result = await PauseAdHandler().execute(client, payload)

    client.execute_graph_call.assert_awaited_once()
    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["endpoint"] == "/23847000123"
    assert kwargs["query_params"] == {"status": "PAUSED"}
    assert result["success"] is True
    assert result["modified_ids"] == ["23847000123"]


# activate_ad шлёт POST /{ad_id} с status=ACTIVE.
@pytest.mark.asyncio
async def test_activate_ad_sends_active_status() -> None:
    client = _make_client({"success": True})
    payload = MetaMutationPayload(mutation_kind="activate_ad", target_id="23847000124")

    result = await ActivateAdHandler().execute(client, payload)

    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["endpoint"] == "/23847000124"
    assert kwargs["query_params"] == {"status": "ACTIVE"}
    assert result["modified_ids"] == ["23847000124"]


# Нечисловой target_id отвергается на валидации.
@pytest.mark.asyncio
async def test_pause_ad_rejects_non_numeric_target() -> None:
    client = _make_client()
    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="abc")

    with pytest.raises(ValueError, match="только из цифр"):
        await PauseAdHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# ====================== pause_campaign / activate_campaign ======================


# pause_campaign шлёт POST /{campaign_id} с status=PAUSED.
@pytest.mark.asyncio
async def test_pause_campaign_uses_campaign_endpoint() -> None:
    client = _make_client({"success": True})
    payload = MetaMutationPayload(mutation_kind="pause_campaign", target_id="23843001")

    await PauseCampaignHandler().execute(client, payload)

    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["endpoint"] == "/23843001"
    assert kwargs["query_params"] == {"status": "PAUSED"}


# activate_campaign шлёт POST /{campaign_id} с status=ACTIVE.
@pytest.mark.asyncio
async def test_activate_campaign_sends_active() -> None:
    client = _make_client({"success": True})
    payload = MetaMutationPayload(mutation_kind="activate_campaign", target_id="23843002")

    await ActivateCampaignHandler().execute(client, payload)

    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["endpoint"] == "/23843002"
    assert kwargs["query_params"] == {"status": "ACTIVE"}


# ====================== set_adset_budget ======================


# daily_budget=5000 центов сериализуется как str "5000" (Graph требует строки).
@pytest.mark.asyncio
async def test_set_adset_budget_daily_serializes_as_string() -> None:
    client = _make_client({"id": "23847005"})
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847005",
        params={"daily_budget": 5000},
    )

    await SetAdsetBudgetHandler().execute(client, payload)

    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["endpoint"] == "/23847005"
    assert kwargs["query_params"] == {"daily_budget": "5000"}
    # Подчёркнуто: значение — строка, не число.
    assert isinstance(kwargs["query_params"]["daily_budget"], str)


# lifetime_budget требует end_time — без него ValueError.
@pytest.mark.asyncio
async def test_set_adset_budget_lifetime_without_end_time_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847005",
        params={"lifetime_budget": 100000},
    )

    with pytest.raises(ValueError, match="end_time"):
        await SetAdsetBudgetHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# lifetime_budget + end_time → правильные form params.
@pytest.mark.asyncio
async def test_set_adset_budget_lifetime_with_end_time() -> None:
    client = _make_client({"success": True})
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847005",
        params={"lifetime_budget": 100000, "end_time": "2026-12-31T23:59:59-0800"},
    )

    await SetAdsetBudgetHandler().execute(client, payload)

    qparams = client.execute_graph_call.call_args.kwargs["query_params"]
    assert qparams["lifetime_budget"] == "100000"
    assert qparams["end_time"] == "2026-12-31T23:59:59-0800"


# Передача обоих daily+lifetime — ValueError.
@pytest.mark.asyncio
async def test_set_adset_budget_both_kinds_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847005",
        params={"daily_budget": 5000, "lifetime_budget": 100000},
    )

    with pytest.raises(ValueError, match="ровно одно"):
        await SetAdsetBudgetHandler().execute(client, payload)


# Отрицательный бюджет — ValueError.
@pytest.mark.asyncio
async def test_set_adset_budget_negative_rejected() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847005",
        params={"daily_budget": -1},
    )

    with pytest.raises(ValueError, match="должен быть > 0"):
        await SetAdsetBudgetHandler().execute(client, payload)


# Bool вместо int — ValueError (bool это subclass int, явно отсекаем).
@pytest.mark.asyncio
async def test_set_adset_budget_rejects_bool_input() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847005",
        params={"daily_budget": True},
    )

    with pytest.raises(ValueError, match="int"):
        await SetAdsetBudgetHandler().execute(client, payload)


# ====================== duplicate_campaign ======================


# duplicate шлёт POST /{id}/copies с deep_copy и status_option.
@pytest.mark.asyncio
async def test_duplicate_campaign_endpoint_and_params() -> None:
    client = _make_client({"copied_campaign_id": "23843999"})
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": True, "status_after_clone": "PAUSED"},
    )

    result = await DuplicateCampaignHandler().execute(client, payload)

    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["endpoint"] == "/23843001/copies"
    assert kwargs["query_params"] == {"deep_copy": "true", "status_option": "PAUSED"}
    assert "23843999" in result["modified_ids"]
    assert result["source_campaign_id"] == "23843001"


# status_after_clone=ACTIVE → status_option=ACTIVE_TO_INHERITED.
@pytest.mark.asyncio
async def test_duplicate_campaign_active_maps_to_inherited() -> None:
    client = _make_client({"copied_campaign_id": "23843998"})
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": False, "status_after_clone": "ACTIVE"},
    )

    await DuplicateCampaignHandler().execute(client, payload)

    qparams = client.execute_graph_call.call_args.kwargs["query_params"]
    assert qparams["status_option"] == "ACTIVE_TO_INHERITED"
    assert qparams["deep_copy"] == "false"


# new_name → один Batch-запрос с copy+rename entries, success=True, new_name в result.
@pytest.mark.asyncio
async def test_duplicate_campaign_with_new_name_does_rename() -> None:
    import json as _json

    copied_id = "23843999"
    batch_resp = [
        {"code": 200, "body": _json.dumps({"copied_campaign_id": copied_id})},
        {"code": 200, "body": _json.dumps({"success": True})},
    ]
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value=batch_resp)
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": True, "new_name": "Cloned R2"},
    )

    result = await DuplicateCampaignHandler().execute(client, payload)

    # Один вызов (Batch API)
    client.execute_graph_call.assert_awaited_once()
    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["endpoint"] == "/"
    assert result["success"] is True
    assert result.get("new_name") == "Cloned R2"
    assert copied_id in result["modified_ids"]


# Если rename упал — копия создана, success=False, last_error содержит «копия создана».
@pytest.mark.asyncio
async def test_duplicate_campaign_rename_failure_returns_warning() -> None:
    import json as _json

    copied_id = "23843999"
    batch_resp = [
        {"code": 200, "body": _json.dumps({"copied_campaign_id": copied_id})},
        {"code": 400, "body": _json.dumps({"error": {"message": "rename failed", "code": 100}})},
    ]
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value=batch_resp)
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": True, "new_name": "Cloned"},
    )

    result = await DuplicateCampaignHandler().execute(client, payload)

    assert result["success"] is False
    assert "копия создана" in result["last_error"]
    assert copied_id in result["modified_ids"]


# Невалидный status_after_clone — ValueError.
@pytest.mark.asyncio
async def test_duplicate_campaign_invalid_status_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": True, "status_after_clone": "DRAFT"},
    )

    with pytest.raises(ValueError, match="PAUSED или ACTIVE"):
        await DuplicateCampaignHandler().execute(client, payload)


# ====================== bulk_status_change ======================


# Полная форма: object_ids + status + object_type.
@pytest.mark.asyncio
async def test_bulk_full_form_sends_batch_endpoint() -> None:
    # Batch response: 2 sub-успеха, 1 fail.
    client = _make_client(
        [
            {"code": 200, "body": "{}"},
            {"code": 200, "body": "{}"},
            {"code": 400, "body": '{"error":{"message":"oops"}}'},
        ]
    )
    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:3",
        params={
            "object_ids": ["23847001", "23847002", "23847003"],
            "status": "PAUSED",
            "object_type": "ad",
        },
    )

    result = await BulkStatusChangeHandler().execute(client, payload)

    kwargs = client.execute_graph_call.call_args.kwargs
    # Batch API — root endpoint, метод POST.
    assert kwargs["method"] == "POST"
    assert kwargs["endpoint"] == "/"
    # batch param — JSON-строка списка sub-requests.
    batch = json.loads(kwargs["query_params"]["batch"])
    assert len(batch) == 3
    assert batch[0]["method"] == "POST"
    assert batch[0]["relative_url"] == "23847001?status=PAUSED"
    # Результаты разделены на success/failed.
    assert result["batch_size"] == 3
    assert result["succeeded"] == 2
    assert result["failed"] == 1
    assert sorted(result["modified_ids"]) == ["23847001", "23847002"]


# Сокращённая форма: ad_ids + action="pause".
@pytest.mark.asyncio
async def test_bulk_drafts_form_maps_pause_to_paused() -> None:
    client = _make_client(
        [
            {"code": 200, "body": "{}"},
            {"code": 200, "body": "{}"},
        ]
    )
    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:2",
        params={"ad_ids": ["23847001", "23847002"], "action": "pause"},
    )

    result = await BulkStatusChangeHandler().execute(client, payload)

    batch = json.loads(client.execute_graph_call.call_args.kwargs["query_params"]["batch"])
    assert all("status=PAUSED" in b["relative_url"] for b in batch)
    assert result["status_applied"] == "PAUSED"
    assert result["object_type"] == "ad"


# Дубликаты в object_ids дедуплицируются.
@pytest.mark.asyncio
async def test_bulk_dedup_keeps_first_occurrence() -> None:
    client = _make_client(
        [
            {"code": 200, "body": "{}"},
            {"code": 200, "body": "{}"},
        ]
    )
    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:dup",
        params={
            "object_ids": ["23847001", "23847001", "23847002"],
            "status": "ACTIVE",
            "object_type": "ad",
        },
    )

    await BulkStatusChangeHandler().execute(client, payload)

    batch = json.loads(client.execute_graph_call.call_args.kwargs["query_params"]["batch"])
    assert len(batch) == 2  # 23847001 дважды → один раз


# Превышение лимита 50 → ValueError, запрос не отправляется.
@pytest.mark.asyncio
async def test_bulk_exceeds_limit_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:big",
        params={
            "object_ids": [str(10000000 + i) for i in range(51)],
            "status": "PAUSED",
            "object_type": "ad",
        },
    )

    with pytest.raises(ValueError, match="слишком много"):
        await BulkStatusChangeHandler().execute(client, payload)
    client.execute_graph_call.assert_not_awaited()


# Невалидный action → ValueError.
@pytest.mark.asyncio
async def test_bulk_invalid_action_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:bad",
        params={"ad_ids": ["23847001"], "action": "delete"},
    )

    with pytest.raises(ValueError, match="pause/activate"):
        await BulkStatusChangeHandler().execute(client, payload)


# Невалидный object_type → ValueError.
@pytest.mark.asyncio
async def test_bulk_invalid_object_type_raises() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk",
        params={
            "object_ids": ["23847001"],
            "status": "PAUSED",
            "object_type": "creative",
        },
    )

    with pytest.raises(ValueError, match="object_type"):
        await BulkStatusChangeHandler().execute(client, payload)


# ====================== create_campaign ======================
# Полные тесты для new Batch API реализации — см. test_meta_api_create_campaign_full.py
# Здесь оставлены только smoke-тесты совместимости с реестром handlers.


# create_campaign зарегистрирован и принимается dispatcher'ом.
@pytest.mark.asyncio
async def test_create_campaign_handler_is_registered() -> None:
    handler = CreateCampaignHandler()
    assert handler.mutation_kind == "create_campaign"
