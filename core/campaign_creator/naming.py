# -*- coding: utf-8 -*-
"""Сборка имени кампании по шаблону: CR{iter} | {OFFER} | MV | {date}."""

from __future__ import annotations

from datetime import datetime

OPERATOR_INITIALS = "MV"


def build_campaign_name(*, iter_num: int, offer_code: str, date: str | None = None) -> str:
    """Имя кампании в едином формате.

    date — строка вида '25.03'. Если не передана — берётся текущая дата (DD.MM).
    """
    if date is None:
        date = datetime.now().strftime("%d.%m")
    return f"CR{iter_num} | {offer_code.upper()} | {OPERATOR_INITIALS} | {date}"
