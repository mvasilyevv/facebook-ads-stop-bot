# -*- coding: utf-8 -*-
"""Unit-тесты для core/meta_api/adapters.py и core/meta_api/schemas.py."""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.meta_api.adapters import (
    extract_action_value,
    extract_cost_per_action,
    meta_insights_row_to_scanned_ad_row,
    parse_decimal,
    parse_insights_row_from_dict,
    parse_int,
)
from core.meta_api.schemas import MetaInsightsRow
from core.scanner.models import ScannedAdRow

# ── parse_decimal ──────────────────────────────────────────────────────────────


def test_parse_decimal_valid_string():
    # Стандартная строка-число от Meta — должна парситься без потерь
    assert parse_decimal("1.23") == Decimal("1.23")


def test_parse_decimal_zero_string():
    # "0" — валидное значение, не None
    assert parse_decimal("0") == Decimal("0")


def test_parse_decimal_small_fraction():
    # Очень маленькое значение — точность Decimal должна сохраниться
    assert parse_decimal("0.001") == Decimal("0.001")


def test_parse_decimal_integer_float():
    # float-аргумент (не строка) — должен конвертироваться
    result = parse_decimal(1.5)
    assert result is not None
    assert float(result) == pytest.approx(1.5)


def test_parse_decimal_int_arg():
    # int-аргумент — тоже допустим
    assert parse_decimal(5) == Decimal("5")


def test_parse_decimal_none():
    # None → None, без исключений
    assert parse_decimal(None) is None


def test_parse_decimal_empty_string():
    # Пустая строка → None
    assert parse_decimal("") is None


def test_parse_decimal_whitespace():
    # Строка из пробелов → None
    assert parse_decimal("   ") is None


def test_parse_decimal_invalid():
    # Нечисловая строка → None, без исключений
    assert parse_decimal("abc") is None


def test_parse_decimal_already_decimal():
    # Уже Decimal → возвращает как есть
    d = Decimal("3.14")
    assert parse_decimal(d) is d


# ── parse_int ──────────────────────────────────────────────────────────────────


def test_parse_int_valid_string():
    # Строка-целое число от Meta
    assert parse_int("42") == 42


def test_parse_int_zero():
    # "0" — ноль, не дефолт
    assert parse_int("0") == 0


def test_parse_int_float_string():
    # Meta иногда возвращает "1234.0" — берём целую часть
    assert parse_int("1234.0") == 1234


def test_parse_int_int_arg():
    # int-аргумент — без конвертации
    assert parse_int(99) == 99


def test_parse_int_none():
    # None → дефолт 0
    assert parse_int(None) == 0


def test_parse_int_empty_string():
    # Пустая строка → дефолт 0
    assert parse_int("") == 0


def test_parse_int_invalid():
    # Нечисловое → дефолт 0
    assert parse_int("abc") == 0


def test_parse_int_custom_default():
    # Кастомный дефолт при None
    assert parse_int(None, default=99) == 99


# ── extract_action_value ───────────────────────────────────────────────────────


def test_extract_action_value_empty_list():
    # Пустой список → 0
    assert extract_action_value([], "lead") == 0


def test_extract_action_value_none():
    # None → 0
    assert extract_action_value(None, "lead") == 0


def test_extract_action_value_missing_type():
    # Тип не совпадает → 0
    actions = [{"action_type": "click", "value": "10"}]
    assert extract_action_value(actions, "lead") == 0


def test_extract_action_value_found():
    # Корректный поиск по action_type
    actions = [
        {"action_type": "click", "value": "10"},
        {"action_type": "lead", "value": "5"},
    ]
    assert extract_action_value(actions, "lead") == 5


def test_extract_action_value_first_match():
    # Берёт первое совпадение при дублях
    actions = [
        {"action_type": "lead", "value": "3"},
        {"action_type": "lead", "value": "7"},
    ]
    assert extract_action_value(actions, "lead") == 3


def test_extract_action_value_multiple_types():
    # Несколько разных типов — ищем нужный
    actions = [
        {"action_type": "purchase", "value": "2"},
        {"action_type": "lead", "value": "8"},
        {"action_type": "complete_registration", "value": "4"},
    ]
    assert extract_action_value(actions, "complete_registration") == 4


# ── extract_cost_per_action ────────────────────────────────────────────────────


def test_extract_cost_per_action_empty():
    # Пустой список → None
    assert extract_cost_per_action([], "lead") is None


def test_extract_cost_per_action_none():
    # None → None
    assert extract_cost_per_action(None, "lead") is None


def test_extract_cost_per_action_missing():
    # Тип не найден → None
    cpa = [{"action_type": "purchase", "value": "5.00"}]
    assert extract_cost_per_action(cpa, "lead") is None


def test_extract_cost_per_action_found():
    # Находит нужный action_type и возвращает Decimal
    cpa = [
        {"action_type": "purchase", "value": "3.50"},
        {"action_type": "lead", "value": "2.75"},
    ]
    assert extract_cost_per_action(cpa, "lead") == Decimal("2.75")


def test_extract_cost_per_action_multiple():
    # Несколько записей — находит точный тип
    cpa = [
        {"action_type": "complete_registration", "value": "1.20"},
        {"action_type": "lead", "value": "4.80"},
    ]
    assert extract_cost_per_action(cpa, "complete_registration") == Decimal("1.20")


# ── parse_insights_row_from_dict ───────────────────────────────────────────────


def _make_full_raw() -> dict:
    """Полный набор полей, как приходит от Meta API."""
    return {
        "ad_id": "23456789",
        "ad_name": "Test Ad",
        "adset_name": "Test Adset",
        "campaign_name": "Test Campaign",
        "spend": "10.50",
        "impressions": "1000",
        "clicks": "50",
        "cpc": "0.21",
        "ctr": "5.00",
        "cpm": "10.50",
        "frequency": "1.23",
        "reach": "900",
        "outbound_clicks_ctr": "4.50",
        "cost_per_result": "3.50",
        "date_start": "2026-05-26",
        "date_stop": "2026-05-26",
        "actions": [
            {"action_type": "outbound_click", "value": "40"},
            {"action_type": "lead", "value": "7"},
            {"action_type": "complete_registration", "value": "3"},
            {"action_type": "purchase", "value": "2"},
        ],
        "cost_per_action_type": [
            {"action_type": "lead", "value": "1.50"},
            {"action_type": "complete_registration", "value": "3.50"},
            {"action_type": "purchase", "value": "5.25"},
        ],
    }


def test_parse_insights_row_full():
    # Полный набор полей → все поля заполнены корректно
    raw = _make_full_raw()
    row = parse_insights_row_from_dict(raw)

    assert row.ad_id == "23456789"
    assert row.spend == Decimal("10.50")
    assert row.impressions == 1000
    assert row.clicks == 50
    assert row.cpc == Decimal("0.21")
    assert row.ctr == Decimal("5.00")
    assert row.cpm == Decimal("10.50")
    assert row.frequency == Decimal("1.23")
    assert row.reach == 900
    assert row.leads == 7
    assert row.cost_per_lead == Decimal("1.50")
    assert row.registrations == 3
    assert row.cost_per_registration == Decimal("3.50")
    assert row.deposits == 2
    assert row.cost_per_deposit == Decimal("5.25")
    assert row.outbound_clicks == 40
    assert row.cost_per_result == Decimal("3.50")
    assert row.date_start == "2026-05-26"
    assert row.date_stop == "2026-05-26"


def test_parse_insights_row_minimal():
    # Минимальный набор полей (только обязательные id и числа)
    raw = {
        "ad_id": "111",
        "ad_name": "Minimal",
        "adset_name": "AS",
        "campaign_name": "C",
        "spend": "0",
        "impressions": "0",
        "clicks": "0",
        "date_start": "2026-05-01",
        "date_stop": "2026-05-01",
    }
    row = parse_insights_row_from_dict(raw)

    assert row.ad_id == "111"
    assert row.spend == Decimal("0")
    assert row.impressions == 0
    assert row.leads == 0
    assert row.cpc is None
    assert row.reach is None
    assert row.outbound_clicks is None


def test_parse_insights_row_missing_optional():
    # Отсутствующие optional-поля → None / 0, без исключений
    raw = {
        "ad_id": "222",
        "ad_name": "No optionals",
        "adset_name": "AS2",
        "campaign_name": "C2",
        "spend": "5.00",
        "date_start": "2026-05-26",
        "date_stop": "2026-05-26",
    }
    row = parse_insights_row_from_dict(raw)
    assert row.frequency is None
    assert row.cpm is None
    assert row.deposits == 0
    assert row.registrations == 0
    assert row.cost_per_deposit is None


def test_parse_insights_row_missing_actions():
    # Нет ни actions, ни cost_per_action_type → leads/deposits/registrations = 0
    raw = {
        "ad_id": "333",
        "ad_name": "No actions",
        "adset_name": "AS3",
        "campaign_name": "C3",
        "spend": "2.00",
        "date_start": "2026-05-26",
        "date_stop": "2026-05-26",
    }
    row = parse_insights_row_from_dict(raw)
    assert row.leads == 0
    assert row.registrations == 0
    assert row.deposits == 0
    assert row.cost_per_lead is None


# ── meta_insights_row_to_scanned_ad_row ───────────────────────────────────────


def _make_insights_row(**overrides) -> MetaInsightsRow:
    """Фабрика MetaInsightsRow с разумными дефолтами для тестов."""
    defaults = dict(
        ad_id="99887766",
        ad_name="Test Ad Name",
        adset_name="Test Adset",
        campaign_name="DRC_CR2 | MV | 26.05",
        spend=Decimal("15.00"),
        impressions=2000,
        clicks=100,
        cpc=Decimal("0.15"),
        ctr=Decimal("5.00"),
        cpm=Decimal("7.50"),
        frequency=Decimal("1.10"),
        reach=1800,
        outbound_clicks=80,
        outbound_ctr=Decimal("4.00"),
        landing_page_views=70,
        cost_per_landing_page_view=Decimal("0.21"),
        leads=12,
        cost_per_lead=Decimal("1.25"),
        registrations=5,
        cost_per_registration=Decimal("3.00"),
        deposits=3,
        cost_per_deposit=Decimal("5.00"),
        cost_per_result=Decimal("1.25"),
        date_start="2026-05-26",
        date_stop="2026-05-26",
    )
    defaults.update(overrides)
    return MetaInsightsRow(**defaults)


def test_adapter_basic_mapping():
    # Полная конвертация — все поля ScannedAdRow заполнены из MetaInsightsRow
    row = _make_insights_row()
    scanned = meta_insights_row_to_scanned_ad_row(row)

    assert isinstance(scanned, ScannedAdRow)
    assert scanned.fb_ad_id == "99887766"
    assert scanned.campaign_name == "DRC_CR2 | MV | 26.05"
    assert scanned.adset_name == "Test Adset"
    assert scanned.ad_name == "Test Ad Name"
    assert scanned.spend == Decimal("15.00")
    assert scanned.impressions == 2000
    assert scanned.clicks == 100
    assert scanned.cpc == Decimal("0.15")
    assert scanned.ctr == Decimal("5.00")
    assert scanned.cpm == Decimal("7.50")
    assert scanned.frequency == Decimal("1.10")
    assert scanned.reach == 1800
    assert scanned.outbound_clicks == 80
    assert scanned.landing_page_views == 70
    assert scanned.leads == 12
    assert scanned.cost_per_lead == Decimal("1.25")
    assert scanned.registrations == 5
    assert scanned.deposits == 3
    assert scanned.cost_per_result == Decimal("1.25")


def test_adapter_delivery_status_default():
    # По умолчанию delivery_status="active"
    row = _make_insights_row()
    scanned = meta_insights_row_to_scanned_ad_row(row)
    assert scanned.delivery_status == "active"


def test_adapter_delivery_status_custom():
    # Явная передача delivery_status
    row = _make_insights_row()
    scanned = meta_insights_row_to_scanned_ad_row(row, delivery_status="paused")
    assert scanned.delivery_status == "paused"


def test_adapter_budget_empty():
    # budget не возвращается из /insights — должен быть пустой строкой
    row = _make_insights_row()
    scanned = meta_insights_row_to_scanned_ad_row(row)
    assert scanned.budget == ""


def test_adapter_resolved_offer_code_none():
    # resolved_offer_code определяется выше по стеку — здесь всегда None
    row = _make_insights_row()
    scanned = meta_insights_row_to_scanned_ad_row(row)
    assert scanned.resolved_offer_code is None


def test_adapter_zero_spend():
    # Нулевые метрики — spend=0, leads=0, без исключений
    row = _make_insights_row(
        spend=Decimal("0"),
        impressions=0,
        clicks=0,
        cpc=None,
        leads=0,
        deposits=0,
        reach=None,
        outbound_clicks=None,
        landing_page_views=None,
    )
    scanned = meta_insights_row_to_scanned_ad_row(row)
    assert scanned.spend == Decimal("0")
    assert scanned.leads == 0
    assert scanned.reach == 0
    assert scanned.outbound_clicks == 0
    assert scanned.landing_page_views == 0


def test_adapter_none_optionals():
    # Все optional-поля None — конвертируются в 0/None без исключений
    row = _make_insights_row(
        cpc=None,
        ctr=None,
        cpm=None,
        frequency=None,
        reach=None,
        outbound_clicks=None,
        outbound_ctr=None,
        landing_page_views=None,
        cost_per_landing_page_view=None,
        cost_per_lead=None,
        cost_per_registration=None,
        cost_per_deposit=None,
        cost_per_result=None,
    )
    scanned = meta_insights_row_to_scanned_ad_row(row)
    assert scanned.cpc is None
    assert scanned.ctr is None
    assert scanned.cost_per_lead is None
    assert scanned.cost_per_result is None


# ── Golden file: JSON от Meta → MetaInsightsRow → ScannedAdRow ────────────────


# Захардкоженный ответ, имитирующий реальный response["data"][0] от Marketing API
_GOLDEN_RAW: dict = {
    "ad_id": "120215678901234",
    "ad_name": "DRC_CR2 | Video1 | 001",
    "adset_name": "DRC | CIS | 25-45",
    "campaign_name": "CR2 | DRC | MV | Tyver | 26.05",
    "spend": "47.83",
    "impressions": "8412",
    "clicks": "312",
    "cpc": "0.153301",
    "ctr": "3.709344",
    "cpm": "5.686282",
    "frequency": "1.403",
    "reach": "5995",
    "cost_per_result": "6.832857",
    "date_start": "2026-05-26",
    "date_stop": "2026-05-26",
    "actions": [
        {"action_type": "outbound_click", "value": "289"},
        {"action_type": "lead", "value": "7"},
        {"action_type": "complete_registration", "value": "2"},
        {"action_type": "purchase", "value": "1"},
    ],
    "cost_per_action_type": [
        {"action_type": "lead", "value": "6.832857"},
        {"action_type": "complete_registration", "value": "23.915"},
        {"action_type": "purchase", "value": "47.83"},
    ],
}

_GOLDEN_EXPECTED_AD_ID = "120215678901234"
_GOLDEN_EXPECTED_SPEND = Decimal("47.83")
_GOLDEN_EXPECTED_LEADS = 7
_GOLDEN_EXPECTED_CPL = Decimal("6.832857")
_GOLDEN_EXPECTED_DEPOSITS = 1
_GOLDEN_EXPECTED_CPD = Decimal("47.83")
_GOLDEN_EXPECTED_REGISTRATIONS = 2
_GOLDEN_EXPECTED_CPR = Decimal("23.915")
_GOLDEN_EXPECTED_OUTBOUND = 289


def test_golden_meta_response_to_insights_row():
    # Золотой файл: стандартный ответ Meta → MetaInsightsRow с корректными полями
    row = parse_insights_row_from_dict(_GOLDEN_RAW)

    assert row.ad_id == _GOLDEN_EXPECTED_AD_ID
    assert row.spend == _GOLDEN_EXPECTED_SPEND
    assert row.leads == _GOLDEN_EXPECTED_LEADS
    assert row.cost_per_lead == _GOLDEN_EXPECTED_CPL
    assert row.deposits == _GOLDEN_EXPECTED_DEPOSITS
    assert row.cost_per_deposit == _GOLDEN_EXPECTED_CPD
    assert row.registrations == _GOLDEN_EXPECTED_REGISTRATIONS
    assert row.cost_per_registration == _GOLDEN_EXPECTED_CPR
    assert row.outbound_clicks == _GOLDEN_EXPECTED_OUTBOUND
    assert row.impressions == 8412
    assert row.frequency == Decimal("1.403")
    assert row.reach == 5995


def test_golden_insights_row_to_scanned_ad_row():
    # Золотой файл: MetaInsightsRow → ScannedAdRow; evaluator получит правильные значения
    row = parse_insights_row_from_dict(_GOLDEN_RAW)
    scanned = meta_insights_row_to_scanned_ad_row(row, delivery_status="active")

    assert isinstance(scanned, ScannedAdRow)
    assert scanned.fb_ad_id == _GOLDEN_EXPECTED_AD_ID
    assert scanned.campaign_name == "CR2 | DRC | MV | Tyver | 26.05"
    assert scanned.spend == _GOLDEN_EXPECTED_SPEND
    assert scanned.leads == _GOLDEN_EXPECTED_LEADS
    assert scanned.cost_per_lead == _GOLDEN_EXPECTED_CPL
    assert scanned.deposits == _GOLDEN_EXPECTED_DEPOSITS
    assert scanned.impressions == 8412
    assert scanned.delivery_status == "active"
    assert scanned.budget == ""
    assert scanned.resolved_offer_code is None
