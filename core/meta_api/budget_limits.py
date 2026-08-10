"""Exact budget validation for Meta money-changing operations."""

from __future__ import annotations

from decimal import Decimal

from core.money import require_currency_exponent, require_exact_currency_amount

MAX_DAILY_BUDGET_MAJOR = Decimal("100000")


def checked_daily_budget_minor_units(
    value: object,
    *,
    currency: object,
    currency_exponent: object,
) -> tuple[str, int, Decimal, int]:
    """Validate major-unit money and derive the exact Meta integer.

    Meta Graph accepts account-currency minor units.  The conversion is allowed
    only from an explicit reviewed currency/exponent pair; there is no
    two-decimal fallback.
    """

    code, exponent = require_currency_exponent(currency, currency_exponent)
    amount = require_exact_currency_amount(
        value,
        currency=code,
        exponent=exponent,
        field="daily_budget",
        allow_zero=False,
    )
    if amount > MAX_DAILY_BUDGET_MAJOR:
        raise ValueError(f"daily_budget exceeds {MAX_DAILY_BUDGET_MAJOR:f} {code}")
    minor_units = amount.scaleb(exponent)
    if minor_units != minor_units.to_integral_value():
        raise ValueError("daily_budget cannot be represented in Meta minor units")
    return code, exponent, amount, int(minor_units)


__all__ = [
    "MAX_DAILY_BUDGET_MAJOR",
    "checked_daily_budget_minor_units",
]
