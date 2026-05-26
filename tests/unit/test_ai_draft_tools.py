# -*- coding: utf-8 -*-
"""Тесты DRAFT_REQUIRED tools из core/ai_assistant/tools/drafts/."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.ai_assistant.tools.base import RiskLevel, ToolError

# ─── Вспомогательные фабрики ───────────────────────────────────────────────


def _make_task(
    *,
    task_id: str | None = None,
    mutation_kind: str = "set_budget",
    status: str = "DRAFT",
) -> MagicMock:
    """Создаёт мок MetaApiMutationTask для подстановки в create_mutation_task."""
    task = MagicMock()
    task.id = uuid.UUID(task_id) if task_id else uuid.uuid4()
    task.mutation_kind = mutation_kind
    task.status = status
    return task


def _make_session_factory(task: MagicMock) -> MagicMock:
    """Создаёт мок session_factory, чей контекстный менеджер отдаёт AsyncMock-сессию.

    create_mutation_task внутри run() вызывается в `async with session_factory() as db`.
    """
    db = AsyncMock()
    db.commit = AsyncMock()

    # Контекстный менеджер async with session_factory() as db
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=db)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=session_cm)
    return factory, db


# ─── Регистрация в GLOBAL_REGISTRY ────────────────────────────────────────


# Сценарий: после импорта пакета drafts все 4 tool'а должны быть в GLOBAL_REGISTRY.
def test_all_draft_tools_registered() -> None:
    import core.ai_assistant.tools.drafts  # noqa: F401 — side-effect import
    from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

    names = set(GLOBAL_REGISTRY.list_names())
    assert "request_budget_change" in names
    assert "request_clone_campaign" in names
    assert "request_bulk_pause" in names
    assert "request_create_campaign" in names


# Сценарий: все 4 DRAFT-tool'а имеют risk_level = DRAFT_REQUIRED.
def test_all_draft_tools_have_draft_required_risk_level() -> None:
    from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

    draft_tools = {
        "request_budget_change",
        "request_clone_campaign",
        "request_bulk_pause",
        "request_create_campaign",
    }
    for name in draft_tools:
        tool = GLOBAL_REGISTRY.get(name)
        assert tool is not None, f"Tool '{name}' не найден в реестре"
        assert tool.risk_level == RiskLevel.DRAFT_REQUIRED, (
            f"Tool '{name}' должен иметь risk_level=DRAFT_REQUIRED, получили {tool.risk_level}"
        )


# ─── RequestBudgetChangeTool ───────────────────────────────────────────────


# Сценарий: run() с daily_budget_usd создаёт DRAFT-задачу в БД и возвращает task_id.
@pytest.mark.asyncio
async def test_budget_change_creates_draft_task() -> None:
    from core.ai_assistant.tools.drafts.request_budget_change import RequestBudgetChangeTool

    task = _make_task(mutation_kind="set_budget")
    factory, db = _make_session_factory(task)

    with (
        patch(
            "core.ai_assistant.tools.drafts.request_budget_change.get_session_factory",
            return_value=factory,
        ),
        patch(
            "core.ai_assistant.tools.drafts.request_budget_change.create_mutation_task",
            new=AsyncMock(return_value=task),
        ) as mock_create,
    ):
        tool = RequestBudgetChangeTool()
        result = await tool.run(
            {
                "ad_account_id": "act_111",
                "entity_id": "adset_456",
                "entity_type": "adset",
                "daily_budget_usd": 50.0,
                "reason": "Тест повышения бюджета",
            }
        )

    # Задача создана с нужными параметрами
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["mutation_kind"] == "set_budget"
    assert call_kwargs["initial_status"] == "DRAFT"
    assert str(task.id) in result
    assert "set_budget" in result
    assert "Подтвердите в Telegram" in result


# Сценарий: ни daily_budget_usd ни lifetime_budget_usd не указаны → ToolError.
@pytest.mark.asyncio
async def test_budget_change_no_budget_raises_tool_error() -> None:
    from core.ai_assistant.tools.drafts.request_budget_change import RequestBudgetChangeTool

    tool = RequestBudgetChangeTool()
    with pytest.raises(ToolError, match="Укажите один из параметров"):
        await tool.run({"ad_account_id": "act_111", "entity_id": "adset_456"})


# Сценарий: оба бюджета (daily и lifetime) указаны одновременно → ToolError.
@pytest.mark.asyncio
async def test_budget_change_both_budgets_raises_tool_error() -> None:
    from core.ai_assistant.tools.drafts.request_budget_change import RequestBudgetChangeTool

    tool = RequestBudgetChangeTool()
    with pytest.raises(ToolError, match="только один параметр"):
        await tool.run(
            {
                "ad_account_id": "act_111",
                "entity_id": "adset_456",
                "daily_budget_usd": 50.0,
                "lifetime_budget_usd": 500.0,
            }
        )


# Сценарий: конвертация USD → центы: 1.50 USD = 150 центов.
@pytest.mark.asyncio
async def test_budget_change_usd_to_cents_conversion() -> None:
    from core.ai_assistant.tools.drafts.request_budget_change import RequestBudgetChangeTool

    task = _make_task(mutation_kind="set_budget")
    factory, db = _make_session_factory(task)

    captured_payload: dict[str, Any] = {}

    async def fake_create(db_session: Any, **kwargs: Any) -> MagicMock:
        captured_payload.update(kwargs.get("payload", {}))
        return task

    with (
        patch(
            "core.ai_assistant.tools.drafts.request_budget_change.get_session_factory",
            return_value=factory,
        ),
        patch(
            "core.ai_assistant.tools.drafts.request_budget_change.create_mutation_task",
            new=AsyncMock(side_effect=fake_create),
        ),
    ):
        tool = RequestBudgetChangeTool()
        await tool.run(
            {
                "ad_account_id": "act_111",
                "entity_id": "adset_99",
                "daily_budget_usd": 1.50,
            }
        )

    # 1.50 USD = 150 центов
    assert captured_payload.get("daily_budget_cents") == 150


# ─── RequestCloneCampaignTool ──────────────────────────────────────────────


# Сценарий: run() создаёт задачу с mutation_kind="clone_campaign" и deep_copy в payload.
@pytest.mark.asyncio
async def test_clone_campaign_creates_task_with_correct_kind() -> None:
    from core.ai_assistant.tools.drafts.request_clone_campaign import RequestCloneCampaignTool

    task = _make_task(mutation_kind="clone_campaign")
    factory, db = _make_session_factory(task)

    captured_kwargs: dict[str, Any] = {}

    async def fake_create(db_session: Any, **kwargs: Any) -> MagicMock:
        captured_kwargs.update(kwargs)
        return task

    with (
        patch(
            "core.ai_assistant.tools.drafts.request_clone_campaign.get_session_factory",
            return_value=factory,
        ),
        patch(
            "core.ai_assistant.tools.drafts.request_clone_campaign.create_mutation_task",
            new=AsyncMock(side_effect=fake_create),
        ),
    ):
        tool = RequestCloneCampaignTool()
        result = await tool.run(
            {
                "ad_account_id": "act_222",
                "source_campaign_id": "camp_789",
                "target_name": "CR2 | DRC | Copy",
                "deep_copy": True,
            }
        )

    assert captured_kwargs["mutation_kind"] == "clone_campaign"
    assert captured_kwargs["initial_status"] == "DRAFT"
    payload = captured_kwargs["payload"]
    assert payload["deep_copy"] is True
    assert payload["source_campaign_id"] == "camp_789"
    assert payload.get("target_name") == "CR2 | DRC | Copy"
    assert str(task.id) in result


# ─── RequestBulkPauseTool ──────────────────────────────────────────────────


# Сценарий: фильтр возвращает пустой список объявлений → ToolError.
@pytest.mark.asyncio
async def test_bulk_pause_empty_filter_raises_tool_error() -> None:
    from core.ai_assistant.tools.drafts.request_bulk_pause import RequestBulkPauseTool

    factory, db = _make_session_factory(MagicMock())

    # resolve возвращает пустой список (нет ad_ids и нет подходящих в БД)
    with (
        patch(
            "core.ai_assistant.tools.drafts.request_bulk_pause.get_session_factory",
            return_value=factory,
        ),
        patch.object(
            RequestBulkPauseTool,
            "_resolve_ad_ids",
            new=AsyncMock(return_value=[]),
        ),
    ):
        tool = RequestBulkPauseTool()
        with pytest.raises(ToolError, match="не найдено объявлений"):
            await tool.run(
                {
                    "ad_account_id": "act_333",
                    "filter": {},  # пустой фильтр
                }
            )


# Сценарий: с явным списком ad_ids создаётся задача с этим списком в payload.
@pytest.mark.asyncio
async def test_bulk_pause_with_explicit_ad_ids_creates_task() -> None:
    from core.ai_assistant.tools.drafts.request_bulk_pause import RequestBulkPauseTool

    task = _make_task(mutation_kind="bulk_pause")
    factory, db = _make_session_factory(task)

    captured_kwargs: dict[str, Any] = {}

    async def fake_create(db_session: Any, **kwargs: Any) -> MagicMock:
        captured_kwargs.update(kwargs)
        return task

    ad_ids = ["111111", "222222", "333333"]

    with (
        patch(
            "core.ai_assistant.tools.drafts.request_bulk_pause.get_session_factory",
            return_value=factory,
        ),
        patch(
            "core.ai_assistant.tools.drafts.request_bulk_pause.create_mutation_task",
            new=AsyncMock(side_effect=fake_create),
        ),
        patch.object(
            RequestBulkPauseTool,
            "_resolve_ad_ids",
            new=AsyncMock(return_value=ad_ids),
        ),
    ):
        tool = RequestBulkPauseTool()
        result = await tool.run(
            {
                "ad_account_id": "act_333",
                "filter": {"ad_ids": ad_ids},
                "reason": "Тест пакетной паузы",
            }
        )

    assert captured_kwargs["mutation_kind"] == "bulk_pause"
    assert captured_kwargs["initial_status"] == "DRAFT"
    assert captured_kwargs["target_id"] == ""  # bulk — target_id пустой
    payload = captured_kwargs["payload"]
    assert payload["ad_ids"] == ad_ids
    assert str(task.id) in result
    assert "Подтвердите в Telegram" in result


# ─── RequestCreateCampaignTool ─────────────────────────────────────────────


# Сценарий: spec_summary с offer_code создаёт задачу mutation_kind="create_campaign".
@pytest.mark.asyncio
async def test_create_campaign_with_spec_summary_creates_task() -> None:
    from core.ai_assistant.tools.drafts.request_create_campaign import RequestCreateCampaignTool

    task = _make_task(mutation_kind="create_campaign")
    factory, db = _make_session_factory(task)

    captured_kwargs: dict[str, Any] = {}

    async def fake_create(db_session: Any, **kwargs: Any) -> MagicMock:
        captured_kwargs.update(kwargs)
        return task

    with (
        patch(
            "core.ai_assistant.tools.drafts.request_create_campaign.get_session_factory",
            return_value=factory,
        ),
        patch(
            "core.ai_assistant.tools.drafts.request_create_campaign.create_mutation_task",
            new=AsyncMock(side_effect=fake_create),
        ),
    ):
        tool = RequestCreateCampaignTool()
        result = await tool.run(
            {
                "ad_account_id": "act_444",
                "spec_summary": {
                    "offer_code": "DRC_CR2",
                    "countries": ["GP", "MQ"],
                    "daily_budget_usd": 20.0,
                    "objective": "OUTCOME_LEADS",
                },
            }
        )

    assert captured_kwargs["mutation_kind"] == "create_campaign"
    assert captured_kwargs["initial_status"] == "DRAFT"
    payload = captured_kwargs["payload"]
    assert payload["offer_code"] == "DRC_CR2"
    assert str(task.id) in result
    assert "create_campaign" in result


# Сценарий: natural_language_description без spec_summary → ToolError "NL parser в wave 3".
@pytest.mark.asyncio
async def test_create_campaign_nl_description_raises_tool_error() -> None:
    from core.ai_assistant.tools.drafts.request_create_campaign import RequestCreateCampaignTool

    tool = RequestCreateCampaignTool()
    with pytest.raises(ToolError, match="wave 3"):
        await tool.run(
            {
                "ad_account_id": "act_444",
                "natural_language_description": "создай кампанию по DRC_CR2 на Гваделупу",
            }
        )


# ─── Идемпотентность ──────────────────────────────────────────────────────


# Сценарий: два вызова с одинаковыми параметрами → create_mutation_task возвращает
#           существующую запись (благодаря idempotency_key в queue).
@pytest.mark.asyncio
async def test_budget_change_idempotency_returns_existing_task() -> None:
    from core.ai_assistant.tools.drafts.request_budget_change import RequestBudgetChangeTool

    # Оба вызова вернут один и тот же task (имитация idempotency_key)
    fixed_id = uuid.uuid4()
    task = MagicMock()
    task.id = fixed_id

    factory, db = _make_session_factory(task)
    call_count = 0

    async def fake_create(db_session: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return task  # всегда возвращаем тот же объект

    with (
        patch(
            "core.ai_assistant.tools.drafts.request_budget_change.get_session_factory",
            return_value=factory,
        ),
        patch(
            "core.ai_assistant.tools.drafts.request_budget_change.create_mutation_task",
            new=AsyncMock(side_effect=fake_create),
        ),
    ):
        tool = RequestBudgetChangeTool()
        args = {
            "ad_account_id": "act_555",
            "entity_id": "adset_77",
            "daily_budget_usd": 30.0,
        }
        result1 = await tool.run(args)
        result2 = await tool.run(args)

    # Оба вызова прошли через create_mutation_task (идемпотентность — ответственность queue)
    assert call_count == 2
    # Оба результата содержат один и тот же task_id
    assert str(fixed_id) in result1
    assert str(fixed_id) in result2
