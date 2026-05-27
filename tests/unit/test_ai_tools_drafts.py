# -*- coding: utf-8 -*-
"""Unit-тесты DRAFT_REQUIRED tools.

Покрытие — для каждого из 4 tools:
- успешный сценарий: создан правильный MetaMutationPayload + create_draft_task вызван.
- валидация args: bad input → ToolError, БД не трогается.

create_draft_task мокается monkeypatch'ем в локальной namespace каждого модуля.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.ai_assistant.tools.base import ToolContext, ToolError
from core.ai_assistant.tools.drafts.request_budget_change import RequestBudgetChangeTool
from core.ai_assistant.tools.drafts.request_bulk_pause import RequestBulkPauseTool
from core.ai_assistant.tools.drafts.request_clone_campaign import RequestCloneCampaignTool
from core.ai_assistant.tools.drafts.request_create_campaign import RequestCreateCampaignTool
from core.meta_api.schemas import MetaMutationPayload


def _make_ctx() -> ToolContext:
    return ToolContext(client_key="user-1", engine=MagicMock(name="engine"), requested_by="tg:bob")


def _capture_create_draft_task(monkeypatch, module_path: str, *, task_id: int = 777):
    """Подменить create_draft_task в указанном модуле; вернуть холдер для проверки args."""
    captured: dict[str, object] = {}

    async def _fake_create_draft_task(engine, *, payload, requested_by, max_attempts=3):
        captured["engine"] = engine
        captured["payload"] = payload
        captured["requested_by"] = requested_by
        captured["max_attempts"] = max_attempts
        return task_id

    monkeypatch.setattr(f"{module_path}.create_draft_task", _fake_create_draft_task)
    return captured


# ====================== request_budget_change ======================


# daily_budget_usd → mutation_kind=set_adset_budget, daily_budget в центах, requested_by из ctx.
@pytest.mark.asyncio
async def test_budget_change_daily_ok(monkeypatch) -> None:
    captured = _capture_create_draft_task(
        monkeypatch,
        "core.ai_assistant.tools.drafts.request_budget_change",
        task_id=100,
    )
    tool = RequestBudgetChangeTool()
    ctx = _make_ctx()

    result = await tool.run(
        ctx,
        {
            "adset_id": "1234567",
            "ad_account_id": "act_777",
            "daily_budget_usd": 25.5,
            "reason": "повышаем после успешного теста",
        },
    )

    assert "task_id=100" in result
    assert "daily=$25.50" in result
    payload = captured["payload"]
    assert isinstance(payload, MetaMutationPayload)
    assert payload.mutation_kind == "set_adset_budget"
    assert payload.target_id == "1234567"
    assert payload.ad_account_id == "act_777"
    assert payload.params["daily_budget"] == 2550  # 25.50 * 100
    assert payload.params["reason"].startswith("повышаем")
    assert captured["requested_by"] == "tg:bob"


# lifetime_budget_usd работает альтернативно daily; params содержит lifetime_budget в центах.
@pytest.mark.asyncio
async def test_budget_change_lifetime_ok(monkeypatch) -> None:
    captured = _capture_create_draft_task(
        monkeypatch, "core.ai_assistant.tools.drafts.request_budget_change"
    )
    tool = RequestBudgetChangeTool()
    await tool.run(
        _make_ctx(),
        {"adset_id": "999", "lifetime_budget_usd": 200},
    )
    payload = captured["payload"]
    assert payload.params == {"lifetime_budget": 20000}


# Одновременно daily и lifetime → ToolError, create_draft_task не вызывается.
@pytest.mark.asyncio
async def test_budget_change_both_budgets_rejected(monkeypatch) -> None:
    captured = _capture_create_draft_task(
        monkeypatch, "core.ai_assistant.tools.drafts.request_budget_change"
    )
    tool = RequestBudgetChangeTool()
    with pytest.raises(ToolError, match="ровно одно"):
        await tool.run(
            _make_ctx(),
            {"adset_id": "1", "daily_budget_usd": 5, "lifetime_budget_usd": 100},
        )
    assert "payload" not in captured


# Нечисловой adset_id → ToolError.
@pytest.mark.asyncio
async def test_budget_change_bad_adset_id(monkeypatch) -> None:
    _capture_create_draft_task(monkeypatch, "core.ai_assistant.tools.drafts.request_budget_change")
    tool = RequestBudgetChangeTool()
    with pytest.raises(ToolError, match="adset_id"):
        await tool.run(_make_ctx(), {"adset_id": "abc", "daily_budget_usd": 5})


# ====================== request_clone_campaign ======================


# Базовый success: mutation_kind=duplicate_campaign, deep_copy=True, status_after=PAUSED.
@pytest.mark.asyncio
async def test_clone_campaign_ok(monkeypatch) -> None:
    captured = _capture_create_draft_task(
        monkeypatch,
        "core.ai_assistant.tools.drafts.request_clone_campaign",
        task_id=222,
    )
    tool = RequestCloneCampaignTool()
    result = await tool.run(
        _make_ctx(),
        {
            "campaign_id": "23012345",
            "new_name": "CR2 | DRC clone",
            "deep_copy": False,
            "status_after_clone": "ACTIVE",
            "ad_account_id": "act_99",
        },
    )
    assert "task_id=222" in result
    payload = captured["payload"]
    assert payload.mutation_kind == "duplicate_campaign"
    assert payload.target_id == "23012345"
    assert payload.params["deep_copy"] is False
    assert payload.params["status_after_clone"] == "ACTIVE"
    assert payload.params["new_name"] == "CR2 | DRC clone"


# Нечисловой campaign_id → ToolError.
@pytest.mark.asyncio
async def test_clone_campaign_bad_id(monkeypatch) -> None:
    _capture_create_draft_task(monkeypatch, "core.ai_assistant.tools.drafts.request_clone_campaign")
    tool = RequestCloneCampaignTool()
    with pytest.raises(ToolError, match="campaign_id"):
        await tool.run(_make_ctx(), {"campaign_id": "ab"})


# ====================== request_bulk_pause ======================


# Прямой список ad_ids → mutation_kind=bulk_status_change action=pause; ad_ids отсортированы.
@pytest.mark.asyncio
async def test_bulk_pause_direct_ids(monkeypatch) -> None:
    captured = _capture_create_draft_task(
        monkeypatch, "core.ai_assistant.tools.drafts.request_bulk_pause"
    )
    tool = RequestBulkPauseTool()
    result = await tool.run(
        _make_ctx(),
        {"ad_ids": ["1003", "1001", "1002"]},
    )
    assert "task_id" in result
    payload = captured["payload"]
    assert payload.mutation_kind == "bulk_status_change"
    assert payload.params["ad_ids"] == ["1001", "1002", "1003"]
    assert payload.params["action"] == "pause"


# offer_code → tool резолвит ad_ids через _resolve_by_offer (мок static).
@pytest.mark.asyncio
async def test_bulk_pause_via_offer_code(monkeypatch) -> None:
    captured = _capture_create_draft_task(
        monkeypatch, "core.ai_assistant.tools.drafts.request_bulk_pause"
    )

    async def _fake_resolve(engine, offer_code):
        return ["555", "777"]

    monkeypatch.setattr(
        RequestBulkPauseTool, "_resolve_by_offer", staticmethod(_fake_resolve), raising=False
    )

    tool = RequestBulkPauseTool()
    await tool.run(_make_ctx(), {"offer_code": "DRC_CR2"})
    payload = captured["payload"]
    assert payload.params["ad_ids"] == ["555", "777"]
    assert payload.params["resolved_from_offer"] == "DRC_CR2"


# Ни ad_ids, ни offer_code → ToolError, draft не создан.
@pytest.mark.asyncio
async def test_bulk_pause_requires_some_filter(monkeypatch) -> None:
    captured = _capture_create_draft_task(
        monkeypatch, "core.ai_assistant.tools.drafts.request_bulk_pause"
    )
    tool = RequestBulkPauseTool()
    with pytest.raises(ToolError):
        await tool.run(_make_ctx(), {})
    assert "payload" not in captured


# Оба ad_ids + offer_code → ToolError.
@pytest.mark.asyncio
async def test_bulk_pause_both_inputs_rejected(monkeypatch) -> None:
    _capture_create_draft_task(monkeypatch, "core.ai_assistant.tools.drafts.request_bulk_pause")
    tool = RequestBulkPauseTool()
    with pytest.raises(ToolError, match="либо"):
        await tool.run(_make_ctx(), {"ad_ids": ["1"], "offer_code": "X"})


# ====================== request_create_campaign ======================


# Базовый success: mutation_kind=create_campaign, daily_budget в центах, status_after=PAUSED.
@pytest.mark.asyncio
async def test_create_campaign_ok(monkeypatch) -> None:
    captured = _capture_create_draft_task(
        monkeypatch,
        "core.ai_assistant.tools.drafts.request_create_campaign",
        task_id=333,
    )
    tool = RequestCreateCampaignTool()
    result = await tool.run(
        _make_ctx(),
        {
            "ad_account_id": "act_42",
            "name": "CR2 | DRC | MV | new",
            "objective": "OUTCOME_LEADS",
            "daily_budget_usd": 30,
        },
    )
    assert "task_id=333" in result
    payload = captured["payload"]
    assert payload.mutation_kind == "create_campaign"
    assert payload.target_id == "new"
    assert payload.ad_account_id == "act_42"
    assert payload.params["objective"] == "OUTCOME_LEADS"
    assert payload.params["daily_budget"] == 3000
    assert payload.params["status_after_create"] == "PAUSED"
    assert payload.params["special_ad_categories"] == ["NONE"]


# Невалидный objective → ToolError.
@pytest.mark.asyncio
async def test_create_campaign_bad_objective(monkeypatch) -> None:
    _capture_create_draft_task(
        monkeypatch, "core.ai_assistant.tools.drafts.request_create_campaign"
    )
    tool = RequestCreateCampaignTool()
    with pytest.raises(ToolError, match="objective"):
        await tool.run(
            _make_ctx(),
            {
                "ad_account_id": "act_1",
                "name": "test",
                "objective": "MAKE_ME_RICH",
                "daily_budget_usd": 1,
            },
        )


# ad_account_id без 'act_' префикса → ToolError.
@pytest.mark.asyncio
async def test_create_campaign_bad_account(monkeypatch) -> None:
    _capture_create_draft_task(
        monkeypatch, "core.ai_assistant.tools.drafts.request_create_campaign"
    )
    tool = RequestCreateCampaignTool()
    with pytest.raises(ToolError, match="act_"):
        await tool.run(
            _make_ctx(),
            {
                "ad_account_id": "123",
                "name": "test",
                "objective": "OUTCOME_TRAFFIC",
                "daily_budget_usd": 5,
            },
        )


# create_draft_task вернул None (коллизия idempotency) → ToolError.
@pytest.mark.asyncio
async def test_draft_collision_raises_tool_error(monkeypatch) -> None:
    async def _none_create_draft(engine, **kwargs):
        return None

    monkeypatch.setattr(
        "core.ai_assistant.tools.drafts.request_budget_change.create_draft_task",
        _none_create_draft,
    )

    tool = RequestBudgetChangeTool()
    with pytest.raises(ToolError, match="DRAFT"):
        await tool.run(_make_ctx(), {"adset_id": "1", "daily_budget_usd": 5})


# Без ctx.engine → require_engine() поднимает ToolError, draft не создан.
@pytest.mark.asyncio
async def test_no_engine_raises_tool_error(monkeypatch) -> None:
    captured = _capture_create_draft_task(
        monkeypatch, "core.ai_assistant.tools.drafts.request_budget_change"
    )
    tool = RequestBudgetChangeTool()
    ctx = ToolContext(client_key="u")  # engine=None
    with pytest.raises(ToolError, match="engine"):
        await tool.run(ctx, {"adset_id": "1", "daily_budget_usd": 5})
    assert "payload" not in captured


# AsyncMock-моки в общем случае: проверка что create_draft_task асинхронен и await'ится.
@pytest.mark.asyncio
async def test_create_draft_called_with_async(monkeypatch) -> None:
    fake = AsyncMock(return_value=999)
    monkeypatch.setattr(
        "core.ai_assistant.tools.drafts.request_clone_campaign.create_draft_task", fake
    )
    tool = RequestCloneCampaignTool()
    await tool.run(_make_ctx(), {"campaign_id": "42"})
    assert fake.await_count == 1
    kwargs = fake.await_args.kwargs
    assert kwargs["requested_by"] == "tg:bob"
    assert isinstance(kwargs["payload"], MetaMutationPayload)
