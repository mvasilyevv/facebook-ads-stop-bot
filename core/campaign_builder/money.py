"""Exact campaign-money conversion for Meta Marketing API payloads.

The campaign API accepts decimal strings in currency major units.  Meta expects
integer minor units for ``daily_budget`` and ``bid_amount``.  Conversion is
therefore allowed only for currencies whose ISO 4217 exponent is explicitly
listed here; there is deliberately no implicit two-decimal fallback.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from core.money import (
    SUPPORTED_CURRENCY_EXPONENTS,
    UnsupportedCurrencyExponentError,
    currency_exponent,
)

_MAJOR_AMOUNT_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")

SUPPORTED_CAMPAIGN_CURRENCY_EXPONENTS = SUPPORTED_CURRENCY_EXPONENTS


class UnsupportedCampaignCurrencyError(UnsupportedCurrencyExponentError):
    """Currency has no reviewed Meta minor-unit exponent contract."""


class InvalidMajorAmountError(ValueError):
    """Major-unit decimal string cannot be represented exactly in minor units."""


def campaign_currency_exponent(currency: str) -> int:
    """Return the reviewed exponent or fail closed for an unsupported code."""

    code = str(currency or "").strip().upper()
    try:
        return currency_exponent(code)
    except UnsupportedCurrencyExponentError as exc:
        raise UnsupportedCampaignCurrencyError(
            f"currency {code or '<empty>'!r} is not supported for campaign creation"
        ) from exc


def nonnegative_major_amount_to_minor_units(raw: str, *, currency: str) -> int:
    """Convert a non-negative decimal string to exact integer minor units.

    JSON numbers, commas, signs and exponent notation are intentionally not
    accepted by the public schema.  Trailing zeroes beyond the currency
    exponent are harmless; any non-zero precision loss is rejected.
    """

    value = str(raw or "").strip()
    if not _MAJOR_AMOUNT_RE.fullmatch(value):
        raise InvalidMajorAmountError("amount must be a positive decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidMajorAmountError("amount is not a finite decimal") from exc
    if not amount.is_finite() or amount < 0:
        raise InvalidMajorAmountError("amount must be non-negative")

    exponent = campaign_currency_exponent(currency)
    scaled = amount * (Decimal(10) ** exponent)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise InvalidMajorAmountError(
            f"amount has more than {exponent} significant decimal places for {currency}"
        )
    return int(integral)


def major_amount_to_minor_units(raw: str, *, currency: str) -> int:
    """Convert a strictly positive decimal string to exact minor units."""

    minor_units = nonnegative_major_amount_to_minor_units(raw, currency=currency)
    if minor_units <= 0:
        raise InvalidMajorAmountError("amount must be greater than zero")
    return minor_units


def normalize_major_amount(raw: str, *, currency: str) -> str:
    """Return a deterministic fixed-point major-unit representation."""

    minor_units = major_amount_to_minor_units(raw, currency=currency)
    exponent = campaign_currency_exponent(currency)
    amount = Decimal(minor_units).scaleb(-exponent)
    return format(amount, f".{exponent}f")


def minor_units_to_major_amount(value: int, *, currency: str) -> str:
    """Render an integer minor-unit amount as an exact decimal string."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("minor-unit value must be an integer")
    exponent = campaign_currency_exponent(currency)
    amount = Decimal(value).scaleb(-exponent)
    return format(amount, f".{exponent}f")


__all__ = [
    "InvalidMajorAmountError",
    "SUPPORTED_CAMPAIGN_CURRENCY_EXPONENTS",
    "UnsupportedCampaignCurrencyError",
    "campaign_currency_exponent",
    "major_amount_to_minor_units",
    "minor_units_to_major_amount",
    "nonnegative_major_amount_to_minor_units",
    "normalize_major_amount",
]
