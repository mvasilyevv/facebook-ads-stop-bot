# -*- coding: utf-8 -*-
"""Unit-тесты READ_ONLY meta tools (поверх MetaApiClient).

MetaApiClient мокается AsyncMock — без реального gRPC. InsightsFetcher
заменяется через monkeypatch class (потому что внутри tools используется
конструктор `InsightsFetcher(client)`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.ai_assistant.tools.base import ToolContext, ToolError
from core.ai_assistant.tools.meta.find_ads import FindAdsTool
from core.ai_assistant.tools.meta.get_account_health import GetAccountHealthTool
from core.ai_assistant.tools.meta.get_competitor_patterns import GetCompetitorPatternsTool
from core.ai_assistant.tools.meta.get_insights import GetInsightsTool
from core.ai_assistant.tools.meta.get_offer_performance import GetOfferPerformanceTool
from core.meta_api.schemas import MetaInsightsRow


def _meta_row(ad_id: str, **over: Any) -> MetaInsightsRow:
    """Builder MetaInsightsRow с дефолтами для тестов."""
    defaults: dict[str, Any] = dict(
        ad_id=ad_id,
        campaign_id="c1",
        adset_id="a1",
        ad_account_id="act_1",
        spend=Decimal("10.5"),
        impressions=1000,
        clicks=50,
        reach=900,
        cpc=Decimal("0.21"),
        ctr=Decimal("0.05"),
        cpm=Decimal("1.17"),
        frequency=Decimal("1.11"),
        actions={"lead": 5},
    )
    defaults.update(over)
    return MetaInsightsRow(**defaults)


def _ctx_with_client(client: Any, *, engine: Any = None) -> ToolContext:
    return ToolContext(client_key="user", engine=engine, meta_api_client=client)


# ====================== get_insights ======================


# Без ad_ids/campaign_ids идёт fetch_for_request (level=ad), результат форматируется.
@pytest.mark.asyncio
async def test_get_insights_default_level_ad(monkeypatch) -> None:
    fetcher_instance = MagicMock(name="InsightsFetcher")
    fetcher_instance.fetch_for_request = AsyncMock(
        return_value=[_meta_row("100"), _meta_row("101", spend=Decimal("5"))]
    )
    monkeypatch.setattr(
        "core.ai_assistant.tools.meta.get_insights.InsightsFetcher",
        lambda client: fetcher_instance,
    )

    tool = GetInsightsTool()
    ctx = _ctx_with_client(MagicMock())
    out = await tool.run(ctx, {"ad_account_id": "act_42"})

    fetcher_instance.fetch_for_request.assert_awaited()
    assert "rows=2" in out
    assert "id=100" in out


# ad_ids: вызывается fetch_for_ads. campaign_ids одновременно с ad_ids → ToolError.
@pytest.mark.asyncio
async def test_get_insights_ad_ids_and_collision(monkeypatch) -> None:
    fetcher_instance = MagicMock(name="InsightsFetcher")
    fetcher_instance.fetch_for_ads = AsyncMock(return_value=[_meta_row("200")])
    monkeypatch.setattr(
        "core.ai_assistant.tools.meta.get_insights.InsightsFetcher",
        lambda client: fetcher_instance,
    )

    tool = GetInsightsTool()
    ctx = _ctx_with_client(MagicMock())
    out = await tool.run(ctx, {"ad_account_id": "act_42", "ad_ids": ["200"]})
    assert "id=200" in out

    with pytest.raises(ToolError, match="ad_ids, либо campaign_ids"):
        await tool.run(
            ctx,
            {"ad_account_id": "act_42", "ad_ids": ["1"], "campaign_ids": ["2"]},
        )


# ad_account_id без 'act_' префикса → ToolError.
@pytest.mark.asyncio
async def test_get_insights_bad_account() -> None:
    tool = GetInsightsTool()
    ctx = _ctx_with_client(MagicMock())
    with pytest.raises(ToolError, match="act_"):
        await tool.run(ctx, {"ad_account_id": "wrong"})


# ====================== find_ads ======================


# execute_graph_call вызывается с правильным endpoint и параметрами.
@pytest.mark.asyncio
async def test_find_ads_passes_filtering() -> None:
    client = MagicMock()
    client.execute_graph_call = AsyncMock(
        return_value={
            "data": [
                {
                    "id": "200001",
                    "name": "Ad #1",
                    "effective_status": "ACTIVE",
                    "campaign": {"id": "c1", "name": "Campaign A"},
                    "adset": {"id": "a1", "name": "Adset A"},
                },
            ]
        }
    )
    tool = FindAdsTool()
    ctx = _ctx_with_client(client)

    out = await tool.run(
        ctx,
        {
            "ad_account_id": "act_42",
            "name_contains": "lead",
            "effective_status": ["ACTIVE", "PAUSED"],
        },
    )
    assert "200001" in out
    call_kwargs = client.execute_graph_call.await_args.kwargs
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["endpoint"] == "/act_42/ads"
    assert "filtering" in call_kwargs["query_params"]


# Пустой ответ → дружелюбное сообщение «нет данных».
@pytest.mark.asyncio
async def test_find_ads_empty_response() -> None:
    client = MagicMock()
    client.execute_graph_call = AsyncMock(return_value={"data": []})
    tool = FindAdsTool()
    ctx = _ctx_with_client(client)
    out = await tool.run(ctx, {"ad_account_id": "act_42"})
    assert "нет" in out.lower()


# ====================== get_offer_performance ======================


# Полный путь: оффер найден → fetch_for_request возвращает строки → агрегаты в выводе.
@pytest.mark.asyncio
async def test_get_offer_performance_aggregates(monkeypatch) -> None:
    # БД: возвращаем active offer
    db_row = ("DRC_CR2", "DRC Crash 2", "gambling", True)

    class _Result:
        def first(self):
            return db_row

    class _Conn:
        async def execute(self, *a, **kw):
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    engine = MagicMock()
    engine.connect = MagicMock(return_value=_Conn())

    fetcher_instance = MagicMock()
    fetcher_instance.fetch_for_request = AsyncMock(
        return_value=[
            _meta_row("1", spend=Decimal("20"), impressions=2000, clicks=100, actions={"lead": 4}),
            _meta_row(
                "2",
                spend=Decimal("30"),
                impressions=3000,
                clicks=200,
                actions={"lead": 6, "complete_registration": 3},
            ),
        ]
    )
    monkeypatch.setattr(
        "core.ai_assistant.tools.meta.get_offer_performance.InsightsFetcher",
        lambda client: fetcher_instance,
    )

    tool = GetOfferPerformanceTool()
    ctx = _ctx_with_client(MagicMock(), engine=engine)

    out = await tool.run(
        ctx,
        {"ad_account_id": "act_1", "offer_code": "DRC_CR2", "date_preset": "last_7d"},
    )
    assert "Оффер DRC_CR2" in out
    assert "Spend: $50.00" in out
    assert "Leads: 10" in out


# Оффер не найден → ToolError.
@pytest.mark.asyncio
async def test_get_offer_performance_missing_offer(monkeypatch) -> None:
    class _Conn:
        async def execute(self, *a, **kw):
            class _R:
                def first(self):
                    return None

            return _R()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    engine = MagicMock()
    engine.connect = MagicMock(return_value=_Conn())

    tool = GetOfferPerformanceTool()
    ctx = _ctx_with_client(MagicMock(), engine=engine)

    with pytest.raises(ToolError, match="не найден"):
        await tool.run(ctx, {"ad_account_id": "act_1", "offer_code": "ZZZ"})


# ====================== get_account_health ======================


# Без ad_account_id вызывается list_ad_accounts; ответ форматируется как список.
@pytest.mark.asyncio
async def test_account_health_list_mode() -> None:
    client = MagicMock()
    client.list_ad_accounts = AsyncMock(
        return_value={
            "data": [
                {
                    "id": "act_111",
                    "name": "Acc 1",
                    "account_status": 1,
                    "currency": "USD",
                },
                {
                    "id": "act_222",
                    "name": "Acc 2",
                    "account_status": 2,
                    "currency": "EUR",
                },
            ]
        }
    )
    tool = GetAccountHealthTool()
    ctx = _ctx_with_client(client)
    out = await tool.run(ctx, {})
    assert "act_111" in out
    assert "ACTIVE" in out
    assert "DISABLED" in out


# С ad_account_id: execute_graph_call(/act_X) + fetch_account_summary.
@pytest.mark.asyncio
async def test_account_health_detailed(monkeypatch) -> None:
    client = MagicMock()
    client.execute_graph_call = AsyncMock(
        return_value={
            "name": "Test acc",
            "account_status": 1,
            "currency": "USD",
            "amount_spent": "12345",
            "balance": "100",
            "spend_cap": "0",
            "timezone_name": "Etc/UTC",
        }
    )
    fetcher_instance = MagicMock()
    fetcher_instance.fetch_account_summary = AsyncMock(
        return_value=_meta_row("summary", spend=Decimal("33.21"), impressions=999, clicks=44)
    )
    monkeypatch.setattr(
        "core.ai_assistant.tools.meta.get_account_health.InsightsFetcher",
        lambda client: fetcher_instance,
    )

    tool = GetAccountHealthTool()
    ctx = _ctx_with_client(client)
    out = await tool.run(ctx, {"ad_account_id": "act_777"})
    assert "act_777" in out
    assert "ACTIVE" in out
    assert "Today: spend=$33.21" in out


# Bad account format → ToolError.
@pytest.mark.asyncio
async def test_account_health_bad_account() -> None:
    tool = GetAccountHealthTool()
    ctx = _ctx_with_client(MagicMock())
    with pytest.raises(ToolError, match="act_"):
        await tool.run(ctx, {"ad_account_id": "777"})


# ====================== get_competitor_patterns ======================


# Заглушка возвращает строку с упоминанием Этапа 4 и /spy.
@pytest.mark.asyncio
async def test_competitor_patterns_stub_response() -> None:
    tool = GetCompetitorPatternsTool()
    ctx = _ctx_with_client(MagicMock())
    out = await tool.run(ctx, {"slot": "chicken road 2", "country": "ke"})
    assert "Этап 4" in out or "Этапе 4" in out
    assert "/spy" in out
