# -*- coding: utf-8 -*-
"""Регрессия HIGH #11: set_adset_budget отбрасывает hallucinated значения.

AI/MCP-tool может прислать $100k+/день — без верхнего порога это привело бы к
мгновенному выгоранию бюджета. Handler должен бросить ValueError ДО исполнения
Graph API вызова.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.meta_api.mutations.set_adset_budget import (
    MAX_DAILY_BUDGET_CENTS,
    MAX_LIFETIME_BUDGET_CENTS,
    SetAdsetBudgetHandler,
)
from core.meta_api.schemas import MetaMutationPayload


def _make_client() -> AsyncMock:
    """Мок-клиент, который ничего не должен получить если валидация падает."""
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value={"success": True})
    return client


# Адекватный бюджет проходит — Graph API вызывается.
@pytest.mark.asyncio
async def test_daily_budget_within_cap_ok() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847000001",
        params={"daily_budget": 5_000},  # $50/день
    )
    result = await SetAdsetBudgetHandler().execute(client, payload)
    assert result["success"] is True
    client.execute_graph_call.assert_awaited_once()


# Daily ровно на границе допустим (boundary-тест).
@pytest.mark.asyncio
async def test_daily_budget_at_cap_ok() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847000001",
        params={"daily_budget": MAX_DAILY_BUDGET_CENTS},
    )
    await SetAdsetBudgetHandler().execute(client, payload)
    client.execute_graph_call.assert_awaited_once()


# Превышение daily-cap → ValueError, Graph API не дёргается.
@pytest.mark.asyncio
async def test_daily_budget_above_cap_rejected() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847000001",
        params={"daily_budget": MAX_DAILY_BUDGET_CENTS + 1},
    )
    with pytest.raises(ValueError, match="превышает разумный лимит"):
        await SetAdsetBudgetHandler().execute(client, payload)
    client.execute_graph_call.assert_not_awaited()


# Lifetime ровно на границе допустим.
@pytest.mark.asyncio
async def test_lifetime_budget_at_cap_ok() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847000001",
        params={
            "lifetime_budget": MAX_LIFETIME_BUDGET_CENTS,
            "end_time": "2026-12-31T23:59:59+0000",
        },
    )
    await SetAdsetBudgetHandler().execute(client, payload)
    client.execute_graph_call.assert_awaited_once()


# Превышение lifetime-cap → ValueError, Graph API не дёргается.
@pytest.mark.asyncio
async def test_lifetime_budget_above_cap_rejected() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847000001",
        params={
            "lifetime_budget": MAX_LIFETIME_BUDGET_CENTS + 1,
            "end_time": "2026-12-31T23:59:59+0000",
        },
    )
    with pytest.raises(ValueError, match="превышает разумный лимит"):
        await SetAdsetBudgetHandler().execute(client, payload)
    client.execute_graph_call.assert_not_awaited()


# 0 центов уже валидировалось как > 0 — на всякий случай sanity-check.
@pytest.mark.asyncio
async def test_zero_budget_still_rejected() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847000001",
        params={"daily_budget": 0},
    )
    with pytest.raises(ValueError, match="должен быть > 0"):
        await SetAdsetBudgetHandler().execute(client, payload)
    client.execute_graph_call.assert_not_awaited()
