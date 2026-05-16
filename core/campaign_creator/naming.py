# -*- coding: utf-8 -*-
"""Сборка имени кампании по шаблону: MV | {GEO} | CR{iter} | adset.pro | {date} | {iter}."""

from __future__ import annotations

from datetime import datetime, timedelta

OPERATOR_INITIALS = "MV"
TRACKER_TAG = "adset.pro"


def build_campaign_name(
    *,
    iter_num: int,
    geo_code: str,
    date: str | None = None,
) -> str:
    """Имя кампании в едином формате.

    date — строка вида '13.05'. Если не передана — берётся завтрашняя дата (DD.MM),
    так как кампания запускается на следующие сутки.
    """
    if date is None:
        date = (datetime.now() + timedelta(days=1)).strftime("%d.%m")
    return (
        f"{OPERATOR_INITIALS} | {geo_code.upper()} | CR{iter_num} | "
        f"{TRACKER_TAG} | {date} | {iter_num}"
    )
