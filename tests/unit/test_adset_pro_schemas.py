# -*- coding: utf-8 -*-
"""Unit-тесты core.adset_pro.schemas — DTO roundtrip и парсинг raw-строк."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from core.adset_pro.schemas import (
    EXT_SUB_FIELD_FOR_AD_ID,
    ConversionRow,
    StatsQueryRequest,
    StatsQueryResponse,
)


# StatsQueryRequest должен корректно сериализоваться в payload без ad_id.
def test_stats_query_request_minimal_payload() -> None:
    req = StatsQueryRequest(since=date(2026, 5, 1), until=date(2026, 5, 10))
    payload = req.to_payload()
    assert payload == {"since": "2026-05-01", "until": "2026-05-10"}


# Когда задан ad_id — он попадает в filters под ключом ext_sub6 (контракт).
def test_stats_query_request_with_ad_id_filters_ext_sub6() -> None:
    req = StatsQueryRequest(
        since=date(2026, 5, 1),
        until=date(2026, 5, 10),
        ad_id="23000000111",
    )
    payload = req.to_payload()
    assert payload["filters"] == {EXT_SUB_FIELD_FOR_AD_ID: "23000000111"}


# group_by и extra_filters мерджатся в один payload корректно.
def test_stats_query_request_extra_filters_and_group_by() -> None:
    req = StatsQueryRequest(
        since=date(2026, 5, 1),
        until=date(2026, 5, 10),
        ad_id="111",
        group_by=("country", "ad_id"),
        extra_filters={"event_type": "ftd"},
    )
    payload = req.to_payload()
    assert payload["group_by"] == ["country", "ad_id"]
    # ad_id перезаписывает ext_sub6, плюс добавляется event_type.
    assert payload["filters"][EXT_SUB_FIELD_FOR_AD_ID] == "111"
    assert payload["filters"]["event_type"] == "ftd"


# Перевёрнутый интервал (since > until) — бросаем ValueError на этапе создания.
def test_stats_query_request_rejects_inverted_range() -> None:
    with pytest.raises(ValueError):
        StatsQueryRequest(since=date(2026, 5, 10), until=date(2026, 5, 1))


# ConversionRow.from_api_row парсит ext_sub6 → fb_ad_id и revenue → Decimal.
def test_conversion_row_from_api_row_happy_path() -> None:
    raw = {
        "click_id": "abc-123",
        "ext_sub6": "23000000999",
        "event_type": "ftd",
        "revenue": "42.50",
        "currency": "USD",
        "occurred_at": "2026-05-15T10:30:00Z",
        "extra": "ignored",
    }
    row = ConversionRow.from_api_row(raw)
    assert row.click_id == "abc-123"
    assert row.fb_ad_id == "23000000999"
    assert row.event_type == "ftd"
    assert row.revenue == Decimal("42.50")
    assert row.currency == "USD"
    assert row.occurred_at == datetime(2026, 5, 15, 10, 30, tzinfo=timezone.utc)
    # raw сохраняется целиком (для возможной диагностики).
    assert row.raw["extra"] == "ignored"


# Пустой/невалидный revenue не должен ронять парсинг — становится Decimal(0).
def test_conversion_row_handles_bad_revenue() -> None:
    row = ConversionRow.from_api_row({"click_id": "x", "ext_sub6": "1", "revenue": "not-a-number"})
    assert row.revenue == Decimal(0)


# Отсутствующий ext_sub6 → fb_ad_id == None (а не "None"-строка).
def test_conversion_row_missing_ext_sub6_becomes_none() -> None:
    row = ConversionRow.from_api_row({"click_id": "x", "revenue": "0"})
    assert row.fb_ad_id is None


# Невалидный timestamp не валит парсер — occurred_at остаётся None.
def test_conversion_row_bad_timestamp_yields_none() -> None:
    row = ConversionRow.from_api_row({"click_id": "x", "occurred_at": "not-iso"})
    assert row.occurred_at is None


# StatsQueryResponse.from_api_payload поддерживает ключ data/rows/result.
@pytest.mark.parametrize("rows_key", ["data", "rows", "result"])
def test_stats_query_response_supports_alternate_keys(rows_key: str) -> None:
    payload = {
        rows_key: [
            {"click_id": "1", "ext_sub6": "ad-1", "revenue": "10"},
            {"click_id": "2", "ext_sub6": "ad-2", "revenue": "20"},
        ]
    }
    resp = StatsQueryResponse.from_api_payload(payload)
    assert len(resp.rows) == 2
    assert resp.rows[0].fb_ad_id == "ad-1"
    assert resp.rows[1].revenue == Decimal("20")


# Если в payload нет ни data/rows/result — получаем пустой кортеж, но raw сохранён.
def test_stats_query_response_empty() -> None:
    resp = StatsQueryResponse.from_api_payload({"meta": {"page": 1}})
    assert resp.rows == ()
    assert resp.raw == {"meta": {"page": 1}}
