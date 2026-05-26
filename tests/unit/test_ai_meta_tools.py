# -*- coding: utf-8 -*-
"""Unit-тесты для READ_ONLY meta tools (Этап 3 wave 2).

Все gRPC-клиенты мокированы через AsyncMock — реальных сетевых вызовов нет.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from core.ai_assistant.tools.base import RiskLevel, ToolError

# ── Вспомогательные утилиты ──────────────────────────────────────────────────


def _make_raw_insight(
    ad_id: str = "123",
    ad_name: str = "Test Ad",
    campaign_name: str = "CR2 | Test",
    spend: str = "100.00",
    impressions: int = 5000,
    clicks: int = 100,
    leads: int = 10,
) -> dict:
    """Вспомогательная фабрика сырой insights-строки (формат Meta API)."""
    return {
        "ad_id": ad_id,
        "ad_name": ad_name,
        "campaign_name": campaign_name,
        "adset_name": "Test Adset",
        "spend": spend,
        "impressions": str(impressions),
        "clicks": str(clicks),
        "actions": [{"action_type": "lead", "value": str(leads)}],
        "effective_status": "ACTIVE",
    }


def _make_meta_insights_row(
    ad_id: str = "123",
    ad_name: str = "Test Ad",
    spend: str = "100.00",
    leads: int = 10,
):
    """Создать минимальный MetaInsightsRow dataclass для тестов.

    Использует только поля из реального MetaInsightsRow (без adset_id / campaign_id).
    """
    from core.meta_api.schemas import MetaInsightsRow

    return MetaInsightsRow(
        ad_id=ad_id,
        ad_name=ad_name,
        adset_name="Test Adset",
        campaign_name="CR2 | Test",
        spend=Decimal(spend),
        impressions=5000,
        clicks=100,
        cpc=Decimal("1.00"),
        ctr=Decimal("2.00"),
        cpm=Decimal("20.00"),
        frequency=Decimal("1.5"),
        reach=3000,
        outbound_clicks=None,
        outbound_ctr=None,
        landing_page_views=None,
        cost_per_landing_page_view=None,
        leads=leads,
        cost_per_lead=None,
        registrations=leads // 2,
        cost_per_registration=None,
        deposits=leads // 5,
        cost_per_deposit=None,
        cost_per_result=None,
        date_start="2026-05-26",
        date_stop="2026-05-26",
    )


# ── Регистрация и risk_level ─────────────────────────────────────────────────


# Проверяем, что все 5 meta tools зарегистрированы после импорта пакета.
def test_all_meta_tools_registered():
    """После импорта core.ai_assistant.tools.meta все 5 tools должны быть в GLOBAL_REGISTRY."""
    import core.ai_assistant.tools.meta  # noqa: F401 — регистрация side-effect
    from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

    names = set(GLOBAL_REGISTRY.list_names())
    expected = {
        "get_insights",
        "find_ads",
        "get_offer_performance",
        "get_account_health",
        "get_competitor_patterns",
    }
    assert expected.issubset(names), f"Не найдены tools: {expected - names}"


# Проверяем, что все meta tools имеют risk_level READ_ONLY.
def test_all_meta_tools_are_read_only():
    """Все 5 meta tools должны иметь risk_level=READ_ONLY."""
    import core.ai_assistant.tools.meta  # noqa: F401
    from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

    meta_names = [
        "get_insights",
        "find_ads",
        "get_offer_performance",
        "get_account_health",
        "get_competitor_patterns",
    ]
    for name in meta_names:
        tool = GLOBAL_REGISTRY.get(name)
        assert tool is not None, f"Tool {name!r} не найден"
        assert tool.risk_level == RiskLevel.READ_ONLY, (
            f"Tool {name!r} имеет risk_level={tool.risk_level}, ожидался READ_ONLY"
        )


# ── GetInsightsTool ──────────────────────────────────────────────────────────


# Базовый сценарий: run() с моком клиента возвращает строку-summary.
@pytest.mark.asyncio
async def test_get_insights_run_returns_summary():
    """GetInsightsTool.run() должен вернуть текстовое summary при успешном ответе API."""
    from core.ai_assistant.tools.meta.get_insights import GetInsightsTool

    raw_rows = [
        _make_raw_insight("1", "Ad One", spend="200.00", leads=20),
        _make_raw_insight("2", "Ad Two", spend="100.00", leads=5),
    ]
    mock_client = AsyncMock()
    mock_client.get_insights = AsyncMock(return_value=raw_rows)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "core.ai_assistant.tools.meta.get_insights.MetaApiHighLevelClient",
        return_value=mock_client,
    ):
        tool = GetInsightsTool()
        result = await tool.run({"ad_account_id": "act_123", "date_preset": "today"})

    assert "spend=300.00" in result
    assert "leads=25" in result
    assert "act_123" in result


# Проверяем нормализацию ad_account_id — добавление "act_" префикса.
@pytest.mark.asyncio
async def test_get_insights_normalizes_account_id():
    """GetInsightsTool должен добавлять 'act_' к числовому ad_account_id."""
    from core.ai_assistant.tools.meta.get_insights import GetInsightsTool

    mock_client = AsyncMock()
    mock_client.get_insights = AsyncMock(return_value=[_make_raw_insight()])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "core.ai_assistant.tools.meta.get_insights.MetaApiHighLevelClient",
        return_value=mock_client,
    ):
        tool = GetInsightsTool()
        result = await tool.run({"ad_account_id": "999888777"})

    # Проверяем что get_insights был вызван с act_ ID
    call_args = mock_client.get_insights.call_args
    assert call_args[0][0] == "act_999888777"
    assert "act_999888777" in result


# Пустой ad_account_id должен вызвать ToolError.
@pytest.mark.asyncio
async def test_get_insights_empty_account_id_raises():
    """GetInsightsTool.run() с пустым ad_account_id → ToolError."""
    from core.ai_assistant.tools.meta.get_insights import GetInsightsTool

    tool = GetInsightsTool()
    with pytest.raises(ToolError, match="ad_account_id не указан"):
        await tool.run({"ad_account_id": ""})


# MetaApiError внутри run() должен превращаться в ToolError.
@pytest.mark.asyncio
async def test_get_insights_meta_api_error_wraps_as_tool_error():
    """MetaApiError из get_insights() должен преобразовываться в ToolError."""
    from clients.python_grpc.meta_api_client import MetaApiError
    from core.ai_assistant.tools.meta.get_insights import GetInsightsTool

    mock_client = AsyncMock()
    mock_client.get_insights = AsyncMock(side_effect=MetaApiError("rate limit", code=17))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "core.ai_assistant.tools.meta.get_insights.MetaApiHighLevelClient",
        return_value=mock_client,
    ):
        tool = GetInsightsTool()
        with pytest.raises(ToolError, match="code=17"):
            await tool.run({"ad_account_id": "act_123"})


# Пустой ответ API должен возвращать понятное сообщение.
@pytest.mark.asyncio
async def test_get_insights_empty_response_message():
    """При пустом ответе API — понятное текстовое сообщение (не исключение)."""
    from core.ai_assistant.tools.meta.get_insights import GetInsightsTool

    mock_client = AsyncMock()
    mock_client.get_insights = AsyncMock(return_value=[])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "core.ai_assistant.tools.meta.get_insights.MetaApiHighLevelClient",
        return_value=mock_client,
    ):
        tool = GetInsightsTool()
        result = await tool.run({"ad_account_id": "act_123"})

    assert "данные отсутствуют" in result.lower() or "отсутствует" in result.lower()


# ── FindAdsTool ──────────────────────────────────────────────────────────────


# Фильтрация по spend_gt должна исключать объявления с малым spend.
@pytest.mark.asyncio
async def test_find_ads_filter_by_spend_gt():
    """FindAdsTool фильтрует объявления по spend_gt."""
    from core.ai_assistant.tools.meta.find_ads import FindAdsTool

    raw_rows = [
        _make_raw_insight("1", "Expensive Ad", spend="500.00", leads=50),
        _make_raw_insight("2", "Cheap Ad", spend="10.00", leads=1),
    ]
    mock_client = AsyncMock()
    mock_client.get_insights = AsyncMock(return_value=raw_rows)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "core.ai_assistant.tools.meta.find_ads.MetaApiHighLevelClient",
        return_value=mock_client,
    ):
        tool = FindAdsTool()
        result = await tool.run(
            {
                "ad_account_id": "act_123",
                "filter": {"spend_gt": 100},
            }
        )

    assert "Expensive Ad" in result
    assert "Cheap Ad" not in result


# Фильтрация по cpl_gt должна работать корректно.
@pytest.mark.asyncio
async def test_find_ads_filter_by_cpl_gt():
    """FindAdsTool фильтрует объявления по cpl_gt."""
    from core.ai_assistant.tools.meta.find_ads import FindAdsTool

    raw_rows = [
        # CPL = 500/50 = 10
        _make_raw_insight("1", "Low CPL Ad", spend="500.00", leads=50),
        # CPL = 500/5 = 100
        _make_raw_insight("2", "High CPL Ad", spend="500.00", leads=5),
    ]
    mock_client = AsyncMock()
    mock_client.get_insights = AsyncMock(return_value=raw_rows)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "core.ai_assistant.tools.meta.find_ads.MetaApiHighLevelClient",
        return_value=mock_client,
    ):
        tool = FindAdsTool()
        result = await tool.run(
            {
                "ad_account_id": "act_123",
                "filter": {"cpl_gt": 50},
            }
        )

    assert "High CPL Ad" in result
    assert "Low CPL Ad" not in result


# Без фильтров — возвращать топ N по spend.
@pytest.mark.asyncio
async def test_find_ads_no_filter_returns_top_n():
    """FindAdsTool без фильтров возвращает топ N по spend."""
    from core.ai_assistant.tools.meta.find_ads import FindAdsTool

    raw_rows = [
        _make_raw_insight(str(i), f"Ad {i}", spend=str(i * 10), leads=i) for i in range(1, 20)
    ]
    mock_client = AsyncMock()
    mock_client.get_insights = AsyncMock(return_value=raw_rows)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "core.ai_assistant.tools.meta.find_ads.MetaApiHighLevelClient",
        return_value=mock_client,
    ):
        tool = FindAdsTool()
        result = await tool.run(
            {
                "ad_account_id": "act_123",
                "limit": 5,
            }
        )

    # Топ-5 по spend — Ad 19, 18, 17, 16, 15
    assert "Ad 19" in result
    assert "найдено 5 объявлений" in result


# ── GetOfferPerformanceTool ──────────────────────────────────────────────────


# Мокируем InsightsFetcher.fetch_for_offer — агрегация должна работать корректно.
@pytest.mark.asyncio
async def test_get_offer_performance_aggregates_correctly():
    """GetOfferPerformanceTool корректно агрегирует MetaInsightsRow по офферу."""
    from core.ai_assistant.tools.meta.get_offer_performance import GetOfferPerformanceTool

    rows = [
        _make_meta_insights_row("1", "Ad One", spend="200.00", leads=20),
        _make_meta_insights_row("2", "Ad Two", spend="100.00", leads=5),
    ]

    # Мокируем полностью _fetch_offer_rows чтобы избежать БД-зависимости
    with patch(
        "core.ai_assistant.tools.meta.get_offer_performance._fetch_offer_rows",
        new=AsyncMock(return_value=rows),
    ):
        tool = GetOfferPerformanceTool()
        result = await tool.run({"offer_code": "DRC_CR2"})

    assert "DRC_CR2" in result
    assert "300.00" in result  # total spend
    assert "25" in result  # total leads


# Несуществующий оффер (None из fetcher) → читаемое сообщение.
@pytest.mark.asyncio
async def test_get_offer_performance_not_found():
    """GetOfferPerformanceTool с несуществующим оффером → читаемое сообщение."""
    from core.ai_assistant.tools.meta.get_offer_performance import GetOfferPerformanceTool

    with patch(
        "core.ai_assistant.tools.meta.get_offer_performance._fetch_offer_rows",
        new=AsyncMock(return_value=None),
    ):
        tool = GetOfferPerformanceTool()
        result = await tool.run({"offer_code": "NONEXISTENT_OFFER"})

    assert "не найден" in result.lower() or "nonexistent_offer" in result.lower()
    # Не должен поднимать исключение
    assert isinstance(result, str)


# MetaApiError внутри get_offer_performance → ToolError.
@pytest.mark.asyncio
async def test_get_offer_performance_meta_api_error():
    """MetaApiError при получении данных по офферу → ToolError."""
    from clients.python_grpc.meta_api_client import MetaApiError
    from core.ai_assistant.tools.meta.get_offer_performance import GetOfferPerformanceTool

    with patch(
        "core.ai_assistant.tools.meta.get_offer_performance._fetch_offer_rows",
        new=AsyncMock(side_effect=MetaApiError("token expired", code=190)),
    ):
        tool = GetOfferPerformanceTool()
        with pytest.raises(ToolError, match="code=190"):
            await tool.run({"offer_code": "DRC_CR2"})


# ── GetAccountHealthTool ─────────────────────────────────────────────────────


# Мок health() + query_rate_limit_headroom → правильное summary.
@pytest.mark.asyncio
async def test_get_account_health_returns_summary():
    """GetAccountHealthTool возвращает корректное summary при доступном канале."""
    from clients.python_grpc.meta_api_client import MetaApiHealth
    from core.ai_assistant.tools.meta.get_account_health import GetAccountHealthTool

    mock_health = MetaApiHealth(
        healthy=True,
        current_url="https://adsmanager.facebook.com",
        token_present=True,
        token_length=200,
        detail="OK",
    )

    mock_client = AsyncMock()
    mock_client.health = AsyncMock(return_value=mock_health)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    mock_rate_stats = {
        "total_calls": 50,
        "rate_limited_calls": 2,
        "errored_calls": 1,
        "average_duration_ms": 340,
    }

    with (
        patch(
            "core.ai_assistant.tools.meta.get_account_health.MetaApiHighLevelClient",
            return_value=mock_client,
        ),
        patch(
            "core.ai_assistant.tools.meta.get_account_health._get_rate_limit_stats",
            new=AsyncMock(return_value=mock_rate_stats),
        ),
        patch(
            "core.ai_assistant.tools.meta.get_account_health._get_recent_errors",
            new=AsyncMock(return_value=[]),
        ),
    ):
        tool = GetAccountHealthTool()
        result = await tool.run({"window_minutes": 15})

    assert "HEALTHY" in result
    assert "присутствует" in result
    assert "50" in result  # total_calls
    assert "340 мс" in result


# gRPC недоступен → UNHEALTHY в ответе, не исключение.
@pytest.mark.asyncio
async def test_get_account_health_grpc_unavailable():
    """GetAccountHealthTool при недоступном gRPC → UNHEALTHY-отчёт (не исключение)."""
    from core.ai_assistant.tools.meta.get_account_health import GetAccountHealthTool

    mock_client = AsyncMock()
    mock_client.health = AsyncMock(side_effect=ConnectionRefusedError("gRPC port closed"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "core.ai_assistant.tools.meta.get_account_health.MetaApiHighLevelClient",
            return_value=mock_client,
        ),
        patch(
            "core.ai_assistant.tools.meta.get_account_health._get_rate_limit_stats",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "core.ai_assistant.tools.meta.get_account_health._get_recent_errors",
            new=AsyncMock(return_value=[]),
        ),
    ):
        tool = GetAccountHealthTool()
        result = await tool.run({})

    assert "UNHEALTHY" in result
    assert isinstance(result, str)


# ── GetCompetitorPatternsTool ─────────────────────────────────────────────────


# Заглушка должна возвращать строку с упоминанием "Этап 4".
@pytest.mark.asyncio
async def test_get_competitor_patterns_returns_stub():
    """GetCompetitorPatternsTool.run() → строка-заглушка с 'Этап 4'."""
    from core.ai_assistant.tools.meta.get_competitor_patterns import GetCompetitorPatternsTool

    tool = GetCompetitorPatternsTool()
    result = await tool.run({"vertical": "finance", "country": "UA"})

    assert "Этап 4" in result
    assert isinstance(result, str)


# Заглушка работает и без аргументов.
@pytest.mark.asyncio
async def test_get_competitor_patterns_no_args():
    """GetCompetitorPatternsTool.run({}) без параметров → строка-заглушка."""
    from core.ai_assistant.tools.meta.get_competitor_patterns import GetCompetitorPatternsTool

    tool = GetCompetitorPatternsTool()
    result = await tool.run({})

    assert "пуста" in result.lower() or "этап 4" in result.lower()
