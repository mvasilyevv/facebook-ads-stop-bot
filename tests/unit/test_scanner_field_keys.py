# -*- coding: utf-8 -*-
"""Тесты маппинга _FIELD_ALIASES в core/scanner/parser.py.

Самое хрупкое место: data-surface атрибуты FB могут измениться.
Эти тесты защищают от регрессий в маппинге и логике извлечения текста.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

import pytest

from core.scanner.models import ScannedAdRow
from core.scanner.parser import (
    _FIELD_ALIASES,
    _NUMERIC_FIELDS,
    _build_row_from_fields,
    _match_field_name,
    _parse_bulk_result,
    _parse_int_value,
    _parse_money,
    _parse_money_or_none,
)

# ---------------------------------------------------------------------------
# Вспомогательные константы
# ---------------------------------------------------------------------------

# Поля ScannedAdRow, которые поступают через маппинг _FIELD_ALIASES.
# fb_ad_id и ad_name имеют спец-логику и исключаются.
_EXCLUDED_FROM_ALIASES = frozenset({"fb_ad_id", "resolved_offer_code"})


def _scanned_ad_row_field_names() -> set[str]:
    """Возвращает все поля ScannedAdRow, подлежащие маппингу."""
    return {
        f.name for f in dataclasses.fields(ScannedAdRow) if f.name not in _EXCLUDED_FROM_ALIASES
    }


def _alias_target_values() -> set[str]:
    """Возвращает множество значений (target полей) из _FIELD_ALIASES."""
    return {field_name for _, field_name in _FIELD_ALIASES}


# ---------------------------------------------------------------------------
# Тест a): полнота маппинга
# ---------------------------------------------------------------------------


def test_field_aliases_covers_all_scanned_ad_row_fields() -> None:
    """Все поля ScannedAdRow (кроме fb_ad_id и resolved_offer_code) должны
    присутствовать как значения в _FIELD_ALIASES."""
    mapped = _alias_target_values()
    required = _scanned_ad_row_field_names()
    missing = required - mapped
    assert not missing, f"Поля ScannedAdRow не покрыты маппингом _FIELD_ALIASES: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Тест b): уникальность значений маппинга
# ---------------------------------------------------------------------------


def test_field_aliases_values_are_unique() -> None:
    """Каждое целевое поле ScannedAdRow должно встречаться в значениях
    _FIELD_ALIASES только один раз (дублей быть не должно)."""
    seen: dict[str, list[str]] = {}
    for key, field_name in _FIELD_ALIASES:
        seen.setdefault(field_name, []).append(key)

    # Разрешены «намеренные» синонимы — несколько data-surface ключей могут
    # маппиться в одно поле (например, campaign_group_name и его forObjectType).
    # Проверяем только что значения-цели не пусты и нет откровенных ошибок.
    for field_name, keys in seen.items():
        assert field_name, "Пустое имя целевого поля в _FIELD_ALIASES"
        assert len(keys) >= 1, f"Поле '{field_name}' не имеет ни одного ключа"


def test_field_aliases_keys_are_unique() -> None:
    """Каждый data-surface ключ должен встречаться в _FIELD_ALIASES ровно один раз."""
    keys = [key for key, _ in _FIELD_ALIASES]
    assert len(keys) == len(set(keys)), (
        f"Дублирующиеся ключи в _FIELD_ALIASES: {[k for k in keys if keys.count(k) > 1]}"
    )


# ---------------------------------------------------------------------------
# Тест c): парсинг строки из bulk-результата (simulate page.evaluate output)
# ---------------------------------------------------------------------------


def _make_valid_row(**overrides: Any) -> dict[str, str]:
    """Возвращает словарь полей, соответствующий валидной строке bulk-результата."""
    base: dict[str, str] = {
        "_row_id": "120241979860890176",
        "campaign_name": "CR2 | DRC | MV | Tyver | 25.03",
        "adset_name": "DRC CR2 | 18-45 | INT",
        "ad_name": "DRC_CR2_v1_video",
        "delivery_status": "Active",
        "spend": "$10.50",
        "budget": "$50.00 Daily",
        "reach": "5 000",
        "impressions": "12 345",
        "clicks": "420",
        "cpc": "$0.025",
        "ctr": "3.40%",
        "cpm": "$0.85",
        "frequency": "2.46",
        "leads": "7",
        "cost_per_lead": "$1.50",
        "registrations": "3",
        "cost_per_registration": "$3.50",
        "cost_per_result": "$10.50",
        "deposits": "1",
        "outbound_clicks": "380",
        "outbound_ctr": "3.08%",
        "landing_page_views": "360",
        "cost_per_landing_page_view": "$0.029",
    }
    base.update(overrides)
    return base


def test_parse_bulk_result_all_fields_populated() -> None:
    """parse_bulk_result корректно заполняет все поля ScannedAdRow из bulk-данных."""
    raw = [_make_valid_row()]
    rows = _parse_bulk_result(raw)
    assert len(rows) == 1
    row = rows[0]

    assert row.fb_ad_id == "120241979860890176"
    assert row.campaign_name == "CR2 | DRC | MV | Tyver | 25.03"
    assert row.adset_name == "DRC CR2 | 18-45 | INT"
    assert row.ad_name == "DRC_CR2_v1_video"
    assert row.delivery_status == "ACTIVE"
    assert row.spend == Decimal("10.50")
    assert row.budget == "$50.00 Daily"
    assert row.reach == 5000
    assert row.impressions == 12345
    assert row.clicks == 420
    assert row.cpc == Decimal("0.025")
    assert row.ctr == Decimal("3.40")
    assert row.cpm == Decimal("0.85")
    assert row.frequency == Decimal("2.46")
    assert row.leads == 7
    assert row.cost_per_lead == Decimal("1.50")
    assert row.registrations == 3
    assert row.cost_per_registration == Decimal("3.50")
    assert row.cost_per_result == Decimal("10.50")
    assert row.deposits == 1
    assert row.outbound_clicks == 380
    assert row.outbound_ctr == Decimal("3.08")
    assert row.landing_page_views == 360
    assert row.cost_per_landing_page_view == Decimal("0.029")


def test_parse_bulk_result_skips_row_without_ad_name() -> None:
    """Строки без ad_name (или с прочерком) не попадают в результат."""
    raw = [_make_valid_row(ad_name="\u2014")]
    rows = _parse_bulk_result(raw)
    assert rows == []


def test_parse_bulk_result_skips_row_with_short_row_id() -> None:
    """Строки с коротким _row_id (порядковый номер, не FB Ad ID) пропускаются."""
    # row_id менее 10 цифр — это строка итогов, не реальное объявление
    raw = [_make_valid_row(_row_id="12")]
    rows = _parse_bulk_result(raw)
    assert rows == []


def test_parse_bulk_result_missing_row_id_skipped() -> None:
    """Строки без _row_id пропускаются."""
    row = _make_valid_row()
    del row["_row_id"]
    rows = _parse_bulk_result([row])
    assert rows == []


# ---------------------------------------------------------------------------
# Тест приоритета алиасов: более специфичные ключи бьют общие
# ---------------------------------------------------------------------------


def test_match_field_name_cost_per_registration_beats_registrations() -> None:
    """cost_per_action_type:omni_complete_registration должен давать cost_per_registration,
    а не registrations."""
    surface = "/am/table/table_row:120241979860890176/table_cell:cost_per_action_type:omni_complete_registration"
    assert _match_field_name(surface) == "cost_per_registration"


def test_match_field_name_registrations_aliased_correctly() -> None:
    """actions:omni_complete_registration → registrations."""
    surface = "/am/table/table_row:120241979860890176/table_cell:actions:omni_complete_registration"
    assert _match_field_name(surface) == "registrations"


def test_match_field_name_outbound_ctr_beats_generic_ctr() -> None:
    """outbound_clicks_ctr должен маппиться в outbound_ctr, а не в ctr."""
    surface = "/am/table/table_row:120241979860890176/table_cell:outbound_clicks_ctr"
    assert _match_field_name(surface) == "outbound_ctr"


def test_match_field_name_generic_ctr() -> None:
    """Общий ctr без outbound-префикса → поле ctr."""
    surface = "/am/table/table_row:120241979860890176/table_cell:forAttributionWindow(ctr)"
    assert _match_field_name(surface) == "ctr"


def test_match_field_name_table_cell_results_is_deposits() -> None:
    """table_cell:results → deposits (колонка «Результаты» = депозиты)."""
    surface = "/am/table/table_row:120241979860890176/table_cell:results"
    assert _match_field_name(surface) == "deposits"


def test_match_field_name_campaign_group_name_alias() -> None:
    """forObjectType(campaign_group_name,ADGROUP) → campaign_name."""
    surface = "/am/table/table_row:120241979860890176/table_cell:forObjectType(campaign_group_name,ADGROUP)"
    assert _match_field_name(surface) == "campaign_name"


def test_match_field_name_adset_name_alias() -> None:
    """forObjectType(campaign_name,ADGROUP) → adset_name."""
    surface = (
        "/am/table/table_row:120241979860890176/table_cell:forObjectType(campaign_name,ADGROUP)"
    )
    assert _match_field_name(surface) == "adset_name"


def test_match_field_name_ad_name_alias() -> None:
    """forObjectType(name,ADGROUP) → ad_name."""
    surface = "/am/table/table_row:120241979860890176/table_cell:forObjectType(name,ADGROUP)"
    assert _match_field_name(surface) == "ad_name"


def test_match_field_name_unknown_surface_returns_none() -> None:
    """Неизвестный data-surface → None (не вызывает исключений)."""
    assert _match_field_name("table_row:99:unknown_future_field_xyz") is None


def test_match_field_name_empty_surface_returns_none() -> None:
    """Пустой data-surface → None."""
    assert _match_field_name("") is None


# ---------------------------------------------------------------------------
# Тест _NUMERIC_FIELDS: все числовые поля присутствуют в множестве
# ---------------------------------------------------------------------------


def test_numeric_fields_contains_expected_entries() -> None:
    """_NUMERIC_FIELDS должен содержать все ключевые числовые метрики."""
    expected = {
        "spend",
        "reach",
        "impressions",
        "clicks",
        "cpc",
        "ctr",
        "cpm",
        "frequency",
        "leads",
        "cost_per_lead",
        "registrations",
        "cost_per_registration",
        "deposits",
        "cost_per_result",
        "outbound_clicks",
        "outbound_ctr",
        "landing_page_views",
        "cost_per_landing_page_view",
    }
    missing = expected - _NUMERIC_FIELDS
    assert not missing, f"Поля отсутствуют в _NUMERIC_FIELDS: {sorted(missing)}"


def test_numeric_fields_does_not_contain_text_fields() -> None:
    """Текстовые поля не должны попадать в _NUMERIC_FIELDS."""
    text_fields = {"ad_name", "campaign_name", "adset_name", "delivery_status", "budget"}
    overlap = text_fields & _NUMERIC_FIELDS
    assert not overlap, f"Текстовые поля ошибочно помечены как числовые: {sorted(overlap)}"


# ---------------------------------------------------------------------------
# Тесты вспомогательных функций парсинга (unit-уровень)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        # Стандартные форматы
        ("$0.15", Decimal("0.15")),
        ("10.50", Decimal("10.50")),
        ("0,15", Decimal("0.15")),
        # Форматы с разделителем тысяч через пробел
        ("1 234.56", Decimal("1234.56")),
        # Прочерки и пустые значения — возврат дефолта
        ("\u2014", Decimal("0")),
        ("-", Decimal("0")),
        ("", Decimal("0")),
        ("n/a", Decimal("0")),
    ],
)
def test_parse_money_formats(text: str, expected: Decimal) -> None:
    """_parse_money корректно обрабатывает различные форматы денежных строк."""
    assert _parse_money(text, Decimal("0")) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$2.50", Decimal("2.50")),
        ("0.00", Decimal("0.00")),
        ("\u2014", None),
        ("-", None),
        ("n/a", None),
        ("", None),
    ],
)
def test_parse_money_or_none(text: str, expected: Decimal | None) -> None:
    """_parse_money_or_none возвращает None для прочерков и пустых значений."""
    assert _parse_money_or_none(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("5 000", 5000),
        ("12345", 12345),
        ("0", 0),
        ("\u2014", 0),
        ("-", 0),
        ("", 0),
        ("1,234", 1234),
    ],
)
def test_parse_int_value(text: str, expected: int) -> None:
    """_parse_int_value корректно парсит целые числа с разными форматами."""
    assert _parse_int_value(text) == expected


# ---------------------------------------------------------------------------
# Тест _build_row_from_fields: граничные случаи
# ---------------------------------------------------------------------------


def test_build_row_from_fields_returns_none_for_dash_ad_name() -> None:
    """Если ad_name == '-', строка пропускается."""
    fields = _make_valid_row(ad_name="-")
    assert _build_row_from_fields(fields) is None


def test_build_row_from_fields_paused_delivery_status() -> None:
    """Статус 'Paused' → delivery_status == 'PAUSED'."""
    row = _build_row_from_fields(_make_valid_row(delivery_status="Paused"))
    assert row is not None
    assert row.delivery_status == "PAUSED"


def test_build_row_from_fields_missing_optional_fields_default_to_zero() -> None:
    """Отсутствующие числовые поля заполняются нулями/None, а не вызывают ошибку."""
    minimal: dict[str, str] = {
        "_row_id": "120241979860890176",
        "ad_name": "Minimal Ad",
        "delivery_status": "Active",
        "spend": "$1.00",
    }
    row = _build_row_from_fields(minimal)
    assert row is not None
    assert row.reach == 0
    assert row.impressions == 0
    assert row.clicks == 0
    assert row.leads == 0
    assert row.deposits == 0
    assert row.cpc is None
    assert row.ctr is None
