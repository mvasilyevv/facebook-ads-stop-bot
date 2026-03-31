# -*- coding: utf-8 -*-
"""Нормализация и совместимость порогов observer по шагам."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

DEFAULT_WARNING_PERCENT_OF_STOP = Decimal("80")
DEFAULT_STOP_PERCENT_OF_BASE = Decimal("100")
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


def extract_observer_threshold_values(source: Any | None = None) -> dict[str, Decimal]:
    """Возвращает полный набор нормализованных порогов observer."""
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


def apply_observer_threshold_values(target: Any, values: dict[str, Decimal]) -> None:
    """Применяет нормализованные пороги к ORM-объекту."""
    target.warning_percent_of_stop = values["warning_percent_of_stop"]
    target.stop_percent_of_base = values["stop_percent_of_base"]
    for step in THRESHOLD_STEPS:
        setattr(
            target,
            f"{step}_warning_percent_of_stop",
            values[f"{step}_warning_percent_of_stop"],
        )
        setattr(
            target,
            f"{step}_stop_percent_of_base",
            values[f"{step}_stop_percent_of_base"],
        )


def derive_legacy_warning_percent_of_stop(values: dict[str, Decimal]) -> Decimal:
    """Собирает legacy warning как самый ранний шаг для безопасного fallback."""
    return min(values[f"{step}_warning_percent_of_stop"] for step in THRESHOLD_STEPS)


def derive_legacy_stop_percent_of_base(values: dict[str, Decimal]) -> Decimal:
    """Собирает legacy stop как самый ранний шаг для безопасного fallback."""
    return min(values[f"{step}_stop_percent_of_base"] for step in THRESHOLD_STEPS)


def step_thresholds_are_uniform(values: dict[str, Decimal], *, kind: str) -> bool:
    """Проверяет, одинаковы ли step-level значения warning или stop."""
    if kind == "warning":
        field_suffix = "warning_percent_of_stop"
    else:
        field_suffix = "stop_percent_of_base"
    current_values = [values[f"{step}_{field_suffix}"] for step in THRESHOLD_STEPS]
    return len(set(current_values)) == 1
