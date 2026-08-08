# -*- coding: utf-8 -*-
"""Unit-тесты core.meta_api.adapters — преобразования API JSON → DTO → ScannedAdRow."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.meta_api.adapters import (
    flatten_actions,
    merge_insights_and_ad,
    meta_api_ad_row_to_scanned_row,
    meta_insights_row_from_dict,
)
from core.meta_api.schemas import MetaApiAdRow


# Список dict от Meta превращается в плоский dict[action_type, int].
def test_flatten_actions_basic() -> None:
    actions = [
        {"action_type": "lead", "value": "5"},
        {"action_type": "link_click", "value": "120"},
        {"action_type": "landing_page_view", "value": "80"},
    ]
    result = flatten_actions(actions)
    assert result == {"lead": 5, "link_click": 120, "landing_page_view": 80}


# Пустой/None вход не падает, возвращает {}.
def test_flatten_actions_empty() -> None:
    assert flatten_actions(None) == {}
    assert flatten_actions([]) == {}


# Полный парс одной строки /insights → MetaInsightsRow.
def test_insights_row_from_dict() -> None:
    raw = {
        "ad_id": "120203040506",
        "campaign_id": "120000000000",
        "adset_id": "120000000111",
        "spend": "12.34",
        "impressions": "1500",
        "clicks": "75",
        "reach": "1200",
        "cpc": "0.16",
        "ctr": "5.0",
        "cpm": "8.23",
        "frequency": "1.25",
        "actions": [{"action_type": "lead", "value": "3"}],
        "date_start": "2026-05-27",
        "date_stop": "2026-05-27",
    }
    row = meta_insights_row_from_dict(raw, ad_account_id="act_999")
    assert row.ad_id == "120203040506"
    assert row.spend == Decimal("12.34")
    assert row.impressions == 1500
    assert row.cpc == Decimal("0.16")
    assert row.actions == {"lead": 3}
    assert row.date_start == date(2026, 5, 27)
    # raw сохранён для аудита/отладки
    assert row.raw["spend"] == "12.34"


# Объединение /ads (имена) и /insights (метрики) даёт MetaApiAdRow со всеми полями.
def test_merge_insights_and_ad() -> None:
    insights = meta_insights_row_from_dict(
        {
            "ad_id": "ad_1",
            "campaign_id": "cmp_1",
            "adset_id": "as_1",
            "spend": "10.00",
            "impressions": "1000",
            "clicks": "50",
            "reach": "800",
            "cpc": "0.20",
            "ctr": "5.0",
            "cpm": "10.0",
            "frequency": "1.25",
        },
        ad_account_id="act_42",
    )
    ad = {
        "id": "ad_1",
        "name": "MyAd v1",
        "effective_status": "ACTIVE",
        "campaign": {"id": "cmp_1", "name": "DRC_CR2 | KE"},
        "adset": {"id": "as_1", "name": "EQ_KE"},
    }
    api_row = merge_insights_and_ad(ad=ad, insights=insights, ad_account_id="act_42")
    assert api_row.fb_ad_id == "ad_1"
    assert api_row.name == "MyAd v1"
    assert api_row.campaign_name == "DRC_CR2 | KE"
    assert api_row.adset_name == "EQ_KE"
    assert api_row.effective_status == "ACTIVE"
    assert api_row.spend == Decimal("10.00")


# Маппинг MetaApiAdRow → ScannedAdRow заполняет ключевые метрики и derived поля.
def test_meta_api_ad_row_to_scanned_row_basic() -> None:
    api = MetaApiAdRow(
        fb_ad_id="ad_1",
        fb_campaign_id="cmp",
        fb_adset_id="as",
        ad_account_id="act_1",
        name="Test",
        campaign_name="DRC_CR2 | KE | MV",
        adset_name="EQ_KE",
        effective_status="ACTIVE",
        configured_status="ACTIVE",
        spend=Decimal("20"),
        impressions=2000,
        clicks=100,
        cpc=Decimal("0.20"),
        ctr=Decimal("5.0"),
        cpm=Decimal("10.0"),
        reach=1500,
        frequency=Decimal("1.33"),
        actions={"lead": 4, "link_click": 90, "landing_page_view": 50},
    )
    scanned = meta_api_ad_row_to_scanned_row(api, resolved_offer_code="DRC_CR2")
    assert scanned.fb_ad_id == "ad_1"
    assert scanned.campaign_id == "cmp"
    assert scanned.adset_id == "as"
    assert scanned.delivery_status == "Active"  # маппинг ACTIVE → Active
    assert scanned.spend == Decimal("20")
    assert scanned.leads == 4
    assert scanned.cost_per_lead == Decimal("5")  # 20 / 4
    assert scanned.landing_page_views == 50
    assert scanned.cost_per_landing_page_view == Decimal("0.4")  # 20 / 50
    assert scanned.resolved_offer_code == "DRC_CR2"


# Cost-per-X должно быть None если знаменатель 0.
def test_to_scanned_row_no_zero_division() -> None:
    api = MetaApiAdRow(
        fb_ad_id="ad_2",
        fb_campaign_id=None,
        fb_adset_id=None,
        ad_account_id="act_1",
        name="Test2",
        campaign_name="C",
        adset_name="A",
        effective_status="PAUSED",
        configured_status="PAUSED",
        spend=Decimal("5"),
        impressions=100,
        clicks=0,
        cpc=None,
        ctr=None,
        cpm=None,
        reach=80,
        frequency=None,
        actions={},
    )
    scanned = meta_api_ad_row_to_scanned_row(api)
    assert scanned.campaign_id == ""
    assert scanned.adset_id == ""
    assert scanned.cost_per_lead is None
    assert scanned.cost_per_registration is None
    assert scanned.cost_per_landing_page_view is None
    assert scanned.delivery_status == "Paused"
