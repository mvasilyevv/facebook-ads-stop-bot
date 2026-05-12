# -*- coding: utf-8 -*-
"""Нормализация порогов observer по шагам."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

DEFAULT_WARNING_PERCENT_OF_STOP = Decimal("80")
DEFAULT_STOP_PERCENT_OF_BASE = Decimal("80")
MIN_WARNING_PERCENT_OF_STOP = Decimal("50")
MAX_WARNING_PERCENT_OF_STOP = Decimal("100")
MIN_STOP_PERCENT_OF_BASE = Decimal("1")
MAX_STOP_PERCENT_OF_BASE = Decimal("100")
THRESHOLD_STEPS = ("cpc", "cpl", "cpr")


def _read_value(source: Any, key: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def normalize_warning_percent_of_stop(
    value: Decimal | int | str | None,
    *,
    default: Decimal = DEFAULT_WARNING_PERCENT_OF_STOP,
) -> Decimal:
    """Ограничивает warning-порог допустимым диапазоном."""
    if value is None or value == "":
        return Decimal(default)
    numeric = Decimal(str(value))
    return min(MAX_WARNING_PERCENT_OF_STOP, max(MIN_WARNING_PERCENT_OF_STOP, numeric))


def normalize_stop_percent_of_base(
    value: Decimal | int | str | None,
    *,
    default: Decimal = DEFAULT_STOP_PERCENT_OF_BASE,
) -> Decimal:
    """Ограничивает фактический стоп допустимым диапазоном."""
    if value is None or value == "":
        return Decimal(default)
    numeric = Decimal(str(value))
    return min(MAX_STOP_PERCENT_OF_BASE, max(MIN_STOP_PERCENT_OF_BASE, numeric))


def extract_threshold_values(source: Any) -> dict[str, Decimal]:
    """Возвращает полный набор нормализованных порогов из rule_config оффера."""
    legacy_warning = normalize_warning_percent_of_stop(
        _read_value(source, "warning_percent_of_stop")
    )
    legacy_stop = normalize_stop_percent_of_base(_read_value(source, "stop_percent_of_base"))
    values: dict[str, Decimal] = {
        "warning_percent_of_stop": legacy_warning,
        "stop_percent_of_base": legacy_stop,
    }
    for step in THRESHOLD_STEPS:
        values[f"{step}_warning_percent_of_stop"] = normalize_warning_percent_of_stop(
            _read_value(source, f"{step}_warning_percent_of_stop"),
            default=legacy_warning,
        )
        values[f"{step}_stop_percent_of_base"] = normalize_stop_percent_of_base(
            _read_value(source, f"{step}_stop_percent_of_base"),
            default=legacy_stop,
        )
    return values
