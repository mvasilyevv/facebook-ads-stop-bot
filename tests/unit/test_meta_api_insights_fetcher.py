# -*- coding: utf-8 -*-
"""Unit-тесты для core/meta_api/insights/fetcher.py.

Все вызовы gRPC мокируются через AsyncMock — реального browser-agent не поднимаем.
MetaApiHighLevelClient.get_insights() заменяется на мок во всех тестах.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.meta_api.insights.fetcher import (
    InsightsFetcher,
    fetch_ad_account_summary,
    fetch_breakdown_by_country,
)
from core.meta_api.schemas import MetaInsightsRow
from core.scanner.models import ScannedAdRow

# ── Фикстуры и вспомогательные объекты ─────────────────────────────────────


def _make_raw_insights_row(
    ad_id: str = "111",
    *,
    spend: str = "10.50",
    impressions: str = "1000",
    clicks: str = "50",
    leads: int = 2,
) -> dict:
    """Минимальная сырая строка insights как возвращает Meta Marketing API."""
    actions = [{"action_type": "lead", "value": str(leads)}] if leads else []
    return {
        "ad_id": ad_id,
        "ad_name": f"Ad {ad_id}",
        "adset_name": "Test Adset",
        "campaign_name": "Test Campaign",
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "cpc": "0.21",
        "ctr": "5.0",
        "cpm": "10.5",
        "frequency": "1.2",
        "reach": "900",
        "actions": actions,
        "cost_per_action_type": [],
        "date_start": "2026-05-26",
        "date_stop": "2026-05-26",
    }


def _make_client_mock(raw_rows: list[dict]) -> MagicMock:
    """Создать мок MetaApiHighLevelClient с заданным результатом get_insights."""
    client = MagicMock()
    client.get_insights = AsyncMock(return_value=raw_rows)
    return client


# ── Тесты InsightsFetcher.fetch_for_ad_account ───────────────────────────────


# Проверяем что fetch_for_ad_account парсит raw dicts в MetaInsightsRow
@pytest.mark.asyncio
async def test_fetch_for_ad_account_returns_parsed_rows():
    raw = [_make_raw_insights_row("111"), _make_raw_insights_row("222")]
    client = _make_client_mock(raw)
    fetcher = InsightsFetcher(client)

    result = await fetcher.fetch_for_ad_account("act_123", date_preset="today")

    assert len(result) == 2
    assert all(isinstance(r, MetaInsightsRow) for r in result)
    assert result[0].ad_id == "111"
    assert result[1].ad_id == "222"
    assert result[0].spend == Decimal("10.50")
    assert result[0].impressions == 1000


# Проверяем что параметры level/date_preset/breakdowns передаются в get_insights
@pytest.mark.asyncio
async def test_fetch_for_ad_account_passes_params_to_client():
    client = _make_client_mock([])
    fetcher = InsightsFetcher(client)

    await fetcher.fetch_for_ad_account(
        "act_999",
        level="campaign",
        date_preset="yesterday",
        breakdowns=["country"],
        limit=100,
    )

    client.get_insights.assert_awaited_once_with(
        "act_999",
        level="campaign",
        date_preset="yesterday",
        time_range=None,
        breakdowns=["country"],
        limit=100,
    )


# ── Тесты InsightsFetcher.fetch_for_ads ──────────────────────────────────────


# Проверяем что передаётся фильтр ad.id IN [...]
@pytest.mark.asyncio
async def test_fetch_for_ads_passes_correct_filtering():
    client = _make_client_mock([_make_raw_insights_row("42")])
    fetcher = InsightsFetcher(client)

    result = await fetcher.fetch_for_ads("act_123", ["42", "43"])

    assert len(result) == 1
    # Проверяем что filtering передан правильный
    call_kwargs = client.get_insights.call_args
    filtering_arg = call_kwargs.kwargs.get("filtering") or call_kwargs[1].get("filtering")
    assert filtering_arg == [{"field": "ad.id", "operator": "IN", "value": ["42", "43"]}]


# Проверяем что пустой список ad_ids возвращает пустой результат без вызова API
@pytest.mark.asyncio
async def test_fetch_for_ads_empty_ids_returns_empty_list():
    client = _make_client_mock([])
    fetcher = InsightsFetcher(client)

    result = await fetcher.fetch_for_ads("act_123", [])

    assert result == []
    # get_insights не должен вызываться при пустом списке
    client.get_insights.assert_not_awaited()


# ── Тесты InsightsFetcher.fetch_for_campaigns ────────────────────────────────


# Проверяем level и filtering для fetch_for_campaigns
@pytest.mark.asyncio
async def test_fetch_for_campaigns_passes_level_and_filtering():
    client = _make_client_mock([_make_raw_insights_row("555")])
    fetcher = InsightsFetcher(client)

    await fetcher.fetch_for_campaigns(
        "act_123",
        ["campaign_001", "campaign_002"],
        level="campaign",
        date_preset="last_7d",
    )

    call_kwargs = client.get_insights.call_args
    assert call_kwargs.kwargs.get("level") == "campaign"
    assert call_kwargs.kwargs.get("date_preset") == "last_7d"
    filtering = call_kwargs.kwargs.get("filtering")
    assert filtering is not None
    assert filtering[0]["field"] == "campaign.id"
    assert "campaign_001" in filtering[0]["value"]
    assert "campaign_002" in filtering[0]["value"]


# Проверяем что пустой список campaign_ids возвращает пустой результат
@pytest.mark.asyncio
async def test_fetch_for_campaigns_empty_ids_returns_empty_list():
    client = _make_client_mock([])
    fetcher = InsightsFetcher(client)

    result = await fetcher.fetch_for_campaigns("act_123", [])

    assert result == []
    client.get_insights.assert_not_awaited()


# ── Тесты InsightsFetcher.fetch_for_offer ────────────────────────────────────


# Проверяем что fetch_for_offer находит fb_ad_id через БД и вызывает fetch_for_ads
@pytest.mark.asyncio
async def test_fetch_for_offer_finds_ad_ids_and_fetches():
    # Мокируем _load_fb_ad_ids_for_offer — не нужна реальная БД
    client = _make_client_mock([_make_raw_insights_row("777")])
    fetcher = InsightsFetcher(client)

    mock_db = AsyncMock()
    with patch(
        "core.meta_api.insights.fetcher._load_fb_ad_ids_for_offer",
        AsyncMock(return_value=["777", "888"]),
    ):
        result = await fetcher.fetch_for_offer(
            mock_db,
            "DRC_CR2",
            ad_account_id="act_123",
        )

    assert len(result) == 1
    assert result[0].ad_id == "777"
    # Проверяем что filtering содержит нужные id
    filtering = client.get_insights.call_args.kwargs.get("filtering")
    assert "777" in filtering[0]["value"]
    assert "888" in filtering[0]["value"]


# Проверяем что fetch_for_offer возвращает пустой список если объявления не найдены
@pytest.mark.asyncio
async def test_fetch_for_offer_returns_empty_if_no_ads_in_db():
    client = _make_client_mock([])
    fetcher = InsightsFetcher(client)

    mock_db = AsyncMock()
    with patch(
        "core.meta_api.insights.fetcher._load_fb_ad_ids_for_offer",
        AsyncMock(return_value=[]),
    ):
        result = await fetcher.fetch_for_offer(
            mock_db,
            "UNKNOWN_OFFER",
            ad_account_id="act_123",
        )

    assert result == []
    client.get_insights.assert_not_awaited()


# Проверяем что fetch_for_offer читает fb_account_id из настроек если не передан явно
@pytest.mark.asyncio
async def test_fetch_for_offer_reads_account_id_from_settings():
    client = _make_client_mock([])
    fetcher = InsightsFetcher(client)

    mock_db = AsyncMock()
    with (
        patch(
            "core.meta_api.insights.fetcher._load_fb_ad_ids_for_offer",
            AsyncMock(return_value=[]),
        ),
        patch(
            "core.meta_api.insights.fetcher._get_fb_account_id",
            AsyncMock(return_value="act_from_settings"),
        ) as mock_get_id,
    ):
        await fetcher.fetch_for_offer(mock_db, "MY_OFFER")

    # _get_fb_account_id должен быть вызван
    mock_get_id.assert_awaited_once_with(mock_db)


# ── Тесты InsightsFetcher.fetch_as_scanned_rows ──────────────────────────────


# Проверяем конвертацию в ScannedAdRow через fetch_as_scanned_rows
@pytest.mark.asyncio
async def test_fetch_as_scanned_rows_returns_scanned_ad_rows():
    raw = [_make_raw_insights_row("321")]
    client = _make_client_mock(raw)
    fetcher = InsightsFetcher(client)

    result = await fetcher.fetch_as_scanned_rows("act_123")

    assert len(result) == 1
    row = result[0]
    assert isinstance(row, ScannedAdRow)
    assert row.fb_ad_id == "321"
    assert row.spend == Decimal("10.50")
    # delivery_status по умолчанию "active"
    assert row.delivery_status == "active"


# Проверяем что метрики корректно конвертируются из MetaInsightsRow в ScannedAdRow
@pytest.mark.asyncio
async def test_fetch_as_scanned_rows_metrics_correctly_mapped():
    raw = [_make_raw_insights_row("99", spend="55.00", impressions="5000", clicks="200", leads=7)]
    client = _make_client_mock(raw)
    fetcher = InsightsFetcher(client)

    result = await fetcher.fetch_as_scanned_rows("act_456")

    row = result[0]
    assert row.impressions == 5000
    assert row.clicks == 200
    assert row.leads == 7
    assert row.spend == Decimal("55.00")


# ── Тесты fetch_ad_account_summary ──────────────────────────────────────────


# Проверяем что fetch_ad_account_summary корректно агрегирует несколько строк
@pytest.mark.asyncio
async def test_fetch_ad_account_summary_aggregates_correctly():
    raw = [
        _make_raw_insights_row("1", spend="10.00", impressions="1000", clicks="50", leads=3),
        _make_raw_insights_row("2", spend="20.00", impressions="2000", clicks="100", leads=5),
    ]
    client = _make_client_mock(raw)

    summary = await fetch_ad_account_summary(client, "act_123", date_preset="today")

    assert summary["total_spend"] == Decimal("30.00")
    assert summary["total_impressions"] == 3000
    assert summary["total_clicks"] == 150
    assert summary["total_leads"] == 8


# Проверяем что при пустом результате API summary возвращает нули
@pytest.mark.asyncio
async def test_fetch_ad_account_summary_empty_result():
    client = _make_client_mock([])

    summary = await fetch_ad_account_summary(client, "act_123")

    assert summary["total_spend"] == Decimal("0")
    assert summary["total_impressions"] == 0
    assert summary["total_clicks"] == 0
    assert summary["total_leads"] == 0


# ── Тест обработки ошибок ────────────────────────────────────────────────────


# Проверяем что MetaApiError из get_insights пробрасывается наружу без поглощения
@pytest.mark.asyncio
async def test_fetch_for_ad_account_propagates_meta_api_error():
    from core.meta_api.errors import MetaApiError as DomainMetaApiError

    client = MagicMock()
    # get_insights бросает ошибку — имитируем ошибку от клиента
    client.get_insights = AsyncMock(side_effect=DomainMetaApiError("Rate limit", code=17))
    fetcher = InsightsFetcher(client)

    with pytest.raises(DomainMetaApiError):
        await fetcher.fetch_for_ad_account("act_123")


# Проверяем что произвольное исключение из get_insights тоже пробрасывается
@pytest.mark.asyncio
async def test_fetch_for_ads_propagates_runtime_error():
    client = MagicMock()
    client.get_insights = AsyncMock(side_effect=RuntimeError("Unexpected"))
    fetcher = InsightsFetcher(client)

    with pytest.raises(RuntimeError, match="Unexpected"):
        await fetcher.fetch_for_ads("act_123", ["1", "2"])


# ── Тест fetch_breakdown_by_country ──────────────────────────────────────────


# Проверяем что breakdowns=['country'] передаётся и возвращаются сырые dicts
@pytest.mark.asyncio
async def test_fetch_breakdown_by_country_passes_country_breakdown():
    raw = [{"ad_id": "1", "country": "UA", "spend": "5.00"}]
    client = _make_client_mock(raw)

    result = await fetch_breakdown_by_country(client, "act_123", date_preset="last_7d")

    assert result == raw
    call_kwargs = client.get_insights.call_args
    assert call_kwargs.kwargs.get("breakdowns") == ["country"]
    assert call_kwargs.kwargs.get("date_preset") == "last_7d"
