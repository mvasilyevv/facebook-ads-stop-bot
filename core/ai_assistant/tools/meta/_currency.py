"""Fail-closed currency evidence and formatting for read-only Meta tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from core.meta_api.errors import MetaApiError
from core.money import currency_exponent, currency_quantum, validated_currency_code

_INTEGER = re.compile(r"^-?\d+$")


@dataclass(frozen=True, slots=True)
class ToolCurrency:
    code: str
    exponent: int


def currency_evidence(raw: object) -> ToolCurrency | None:
    """Return reviewed currency evidence or hide all dependent money."""

    code = validated_currency_code(raw)
    if code is None:
        return None
    try:
        exponent = currency_exponent(code)
    except ValueError:
        return None
    return ToolCurrency(code=code, exponent=exponent)


async def fetch_account_currency(
    client: Any,
    ad_account_id: str,
) -> ToolCurrency | None:
    """Fetch current account currency; read failures degrade to unknown money."""

    try:
        response = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{ad_account_id}",
            query_params={"fields": "currency"},
            ad_account_id=ad_account_id,
        )
    except MetaApiError:
        return None
    return currency_evidence(response.get("currency") if isinstance(response, dict) else None)


def format_major_money(
    value: object,
    evidence: ToolCurrency | None,
) -> str:
    """Format a major-unit amount for display, never for a decision."""

    if evidence is None:
        return "—"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    if not amount.is_finite():
        return "—"
    quantum = currency_quantum(evidence.code, evidence.exponent)
    rendered = amount.quantize(quantum, rounding=ROUND_HALF_UP)
    if rendered == 0:
        rendered = abs(rendered)
    return f"{rendered:.{evidence.exponent}f} {evidence.code}"


def format_minor_money(
    value: object,
    evidence: ToolCurrency | None,
) -> str:
    """Format Meta's integer minor-unit account fields with explicit identity."""

    if evidence is None:
        return "—"
    raw = "" if value is None else str(value).strip()
    if not _INTEGER.fullmatch(raw):
        return "—"
    amount = Decimal(raw).scaleb(-evidence.exponent)
    return format_major_money(amount, evidence)


__all__ = [
    "ToolCurrency",
    "currency_evidence",
    "fetch_account_currency",
    "format_major_money",
    "format_minor_money",
]
