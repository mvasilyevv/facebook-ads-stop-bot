# -*- coding: utf-8 -*-
"""Общие математические утилиты: безопасное деление, конвертация Decimal."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

# Точность для cost-метрик (CPC, CPL, CPR, cost_per_deposit)
COST_METRIC_PRECISION = Decimal("0.0001")


def to_decimal(value: object | None, default: Decimal = Decimal("0")) -> Decimal:
    """Безопасная конвертация в Decimal."""
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def to_int(value: object | None, default: int = 0) -> int:
    """Безопасная конвертация в int."""
    if value is None or value == "":
        return default
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return default


def safe_div(
    numerator: Decimal | int,
    denominator: int,
) -> Decimal | None:
    """Безопасное деление, возвращает None при нулевом знаменателе."""
    if not denominator:
        return None
    return Decimal(str(numerator)) / Decimal(str(denominator))


def safe_div_quantized(
    numerator: Decimal | int,
    denominator: int,
    precision: Decimal = COST_METRIC_PRECISION,
) -> Decimal | None:
    """Безопасное деление с квантизацией (для cost-метрик)."""
    if denominator <= 0:
        return None
    return (Decimal(str(numerator)) / Decimal(str(denominator))).quantize(precision)


def safe_div_str(
    numerator: Decimal | int,
    denominator: int,
    precision: Decimal = COST_METRIC_PRECISION,
) -> str | None:
    """Безопасное деление → строка (для JSON-архивов)."""
    result = safe_div_quantized(numerator, denominator, precision)
    return str(result) if result is not None else None


def safe_percent(numerator: int, denominator: int) -> float | None:
    """Конверсия в проценты (для воронки)."""
    if denominator <= 0:
        return None
    return round((float(numerator) / float(denominator)) * 100, 1)
