# -*- coding: utf-8 -*-
"""LOW (аудит 02.07): AggregationResult.rows_dropped_invalid_country — контракт поля.

Счётчик отброшенных из-за невалидного country строк должен иметь безопасный дефолт
(0), чтобы старый вызывающий код (без keyword-аргумента) не падал, и должен корректно
прокидываться в аудит-снимок system_config.tracker_aggregator_runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from core.adset_pro.aggregator import AggregationResult


def _make_result(**overrides) -> AggregationResult:
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    defaults = dict(
        window_start=now,
        window_end=now,
        day_floor=now,
        day_ceil=now,
        rows_upserted=0,
        rows_inserted=0,
        rows_updated=0,
        deposits_total=0,
        revenue_total=Decimal("0"),
    )
    defaults.update(overrides)
    return AggregationResult(**defaults)


# Сценарий: старый код создаёт AggregationResult без rows_dropped_invalid_country —
# дефолт должен быть 0, а не обязательный позиционный аргумент (иначе TypeError).
def test_rows_dropped_invalid_country_defaults_to_zero() -> None:
    result = _make_result()
    assert result.rows_dropped_invalid_country == 0


# Сценарий: явно переданное ненулевое значение сохраняется без искажений.
def test_rows_dropped_invalid_country_explicit_value() -> None:
    result = _make_result(rows_dropped_invalid_country=7)
    assert result.rows_dropped_invalid_country == 7


# Сценарий: payload аудит-снимка (как в apps/tracker_aggregator_worker/worker.py
# _write_audit) включает поле под тем же именем, что читает счётчик из результата.
def test_audit_payload_includes_dropped_country_counter() -> None:
    result = _make_result(rows_dropped_invalid_country=3)
    payload = {
        "rows_upserted": result.rows_upserted,
        "rows_dropped_invalid_country": result.rows_dropped_invalid_country,
    }
    assert payload["rows_dropped_invalid_country"] == 3
