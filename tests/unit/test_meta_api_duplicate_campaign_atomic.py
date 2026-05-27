# -*- coding: utf-8 -*-
"""Unit-тесты DuplicateCampaignHandler — атомарный rename через Batch API.

Покрываем:
- без new_name → простой /copies, один вызов
- с new_name → один POST / с batch=[copy, rename]
- batch оба успешны → success=True, modified_ids с copied_campaign_id
- batch copy ok + rename failed → success=False, last_error со «копия создана», id в modified_ids
- batch copy failed → success=False, modified_ids пуст
- new_name только из пробелов → trim → ветка без batch (как None)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.meta_api.mutations.duplicate_campaign import DuplicateCampaignHandler
from core.meta_api.schemas import MetaMutationPayload


def _make_client(*responses: Any) -> AsyncMock:
    """Мок-клиент: execute_graph_call возвращает responses по очереди."""
    client = AsyncMock()
    if len(responses) == 1:
        client.execute_graph_call = AsyncMock(return_value=responses[0])
    else:
        client.execute_graph_call = AsyncMock(side_effect=list(responses))
    return client


def _batch_response(copy_code: int, rename_code: int, copied_id: str = "23843999") -> list:
    """Вспомогательный: возвращает массив sub-ответов Batch API."""
    copy_body = (
        json.dumps({"copied_campaign_id": copied_id})
        if 200 <= copy_code < 300
        else json.dumps({"error": {"message": "copy error", "code": 100}})
    )
    rename_body = (
        json.dumps({"success": True})
        if 200 <= rename_code < 300
        else json.dumps({"error": {"message": "rename error", "code": 100}})
    )
    return [
        {"code": copy_code, "body": copy_body},
        {"code": rename_code, "body": rename_body},
    ]


# Без new_name → один POST /{id}/copies, success=True, copied_campaign_id в modified_ids.
@pytest.mark.asyncio
async def test_duplicate_without_new_name_single_copies_call() -> None:
    client = _make_client({"copied_campaign_id": "23843999"})
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": True, "status_after_clone": "PAUSED"},
    )

    result = await DuplicateCampaignHandler().execute(client, payload)

    # Только один вызов execute_graph_call
    client.execute_graph_call.assert_awaited_once()
    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["endpoint"] == "/23843001/copies"
    assert kwargs["query_params"]["deep_copy"] == "true"
    assert result["success"] is True
    assert "23843999" in result["modified_ids"]


# С new_name → один POST к "/" с batch из двух entries.
@pytest.mark.asyncio
async def test_duplicate_with_new_name_sends_batch_request() -> None:
    client = _make_client(_batch_response(200, 200, "23843999"))
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": True, "status_after_clone": "PAUSED", "new_name": "Новая кампания"},
    )

    await DuplicateCampaignHandler().execute(client, payload)

    client.execute_graph_call.assert_awaited_once()
    kwargs = client.execute_graph_call.call_args.kwargs
    # Batch API → root endpoint
    assert kwargs["endpoint"] == "/"
    assert kwargs["method"] == "POST"
    batch = json.loads(kwargs["query_params"]["batch"])
    assert len(batch) == 2
    # entry[0]: copies
    assert "23843001/copies" in batch[0]["relative_url"]
    assert batch[0]["method"] == "POST"
    # entry[1]: rename через JSONPath на result=copy
    assert "result=copy" in batch[1]["relative_url"]
    # body — form-encoded, кириллица URL-encoded → проверяем что начинается с "name="
    from urllib.parse import unquote_plus

    body_str = batch[1].get("body", "")
    assert unquote_plus(body_str).startswith("name=")


# Batch оба успешны → success=True, copied_campaign_id в modified_ids.
@pytest.mark.asyncio
async def test_duplicate_batch_both_success_returns_success() -> None:
    copied_id = "99887766"
    client = _make_client(_batch_response(200, 200, copied_id))
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": True, "new_name": "Copy Q4"},
    )

    result = await DuplicateCampaignHandler().execute(client, payload)

    assert result["success"] is True
    assert copied_id in result["modified_ids"]
    assert result.get("new_name") == "Copy Q4"


# Batch copy ok + rename failed → success=False, last_error содержит «копия создана» и id,
# но copied_campaign_id всё равно в modified_ids.
@pytest.mark.asyncio
async def test_duplicate_batch_rename_fail_returns_failure_with_copy_id() -> None:
    copied_id = "55667788"
    client = _make_client(_batch_response(200, 400, copied_id))
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": True, "new_name": "Fail Rename"},
    )

    result = await DuplicateCampaignHandler().execute(client, payload)

    assert result["success"] is False
    assert copied_id in result["modified_ids"]
    assert "копия создана" in result["last_error"]
    assert copied_id in result["last_error"]


# Batch copy failed → success=False, modified_ids пуст.
@pytest.mark.asyncio
async def test_duplicate_batch_copy_fail_returns_failure_empty_ids() -> None:
    client = _make_client(_batch_response(500, 400, ""))
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": True, "new_name": "Never Created"},
    )

    result = await DuplicateCampaignHandler().execute(client, payload)

    assert result["success"] is False
    assert result["modified_ids"] == []
    assert "copy не выполнен" in result["last_error"]


# new_name из одних пробелов → trim → пустая строка → ветка без batch (как None).
@pytest.mark.asyncio
async def test_duplicate_whitespace_only_new_name_goes_simple_path() -> None:
    client = _make_client({"copied_campaign_id": "23843999"})
    payload = MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843001",
        params={"deep_copy": True, "new_name": "   "},
    )

    result = await DuplicateCampaignHandler().execute(client, payload)

    # Только один вызов — простой /copies без batch
    client.execute_graph_call.assert_awaited_once()
    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["endpoint"] == "/23843001/copies"
    assert result["success"] is True
