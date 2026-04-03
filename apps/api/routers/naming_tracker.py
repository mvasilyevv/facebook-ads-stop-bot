# -*- coding: utf-8 -*-
"""Роутер трекера нейминга — отслеживание нумерации объявлений по паттернам."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import (
    NamingPatternAdSchema,
    NamingPatternGroupSchema,
    NamingTrackerResponseSchema,
)
from core.models import AdSnapshot, Offer

router = APIRouter(prefix="/api", tags=["naming-tracker"])

_TRAILING_DIGITS_RE = re.compile(r"^(.*?)(\d+)$")


def _extract_naming_pattern(ad_name: str) -> tuple[str, int] | None:
    """Извлекает префикс и числовой суффикс из названия объявления.

    Args:
        ad_name: Название объявления, например 'DRC_CR2_CR007'.

    Returns:
        Кортеж (prefix, number) или None, если суффикс не найден.
    """
    m = _TRAILING_DIGITS_RE.match(ad_name.strip())
    if not m or not m.group(1):
        return None
    return m.group(1), int(m.group(2))


def _build_pattern_groups(
    snapshots: list[AdSnapshot],
    offers_map: dict[str, str],
) -> list[NamingPatternGroupSchema]:
    """Группирует снэпшоты по (prefix, offer_code) и агрегирует статистику.

    Args:
        snapshots: Список объявлений из БД.
        offers_map: Словарь offer_code -> offer_name.

    Returns:
        Список групп паттернов с макс. номером и примерами.
    """
    grouped: dict[tuple[str, str | None], list[tuple[int, AdSnapshot]]] = defaultdict(list)

    for snap in snapshots:
        parsed = _extract_naming_pattern(snap.ad_name or "")
        if parsed is None:
            continue
        prefix, number = parsed
        key = (prefix, snap.resolved_offer_code)
        grouped[key].append((number, snap))

    result: list[NamingPatternGroupSchema] = []
    for (prefix, offer_code), items in grouped.items():
        items.sort(key=lambda x: x[0], reverse=True)
        max_number = items[0][0]
        seen_names: set[str] = set()
        recent: list[NamingPatternAdSchema] = []
        for _, snap in items:
            name = snap.ad_name or ""
            if name in seen_names:
                continue
            seen_names.add(name)
            recent.append(
                NamingPatternAdSchema(
                    ad_name=name,
                    fb_ad_id=snap.fb_ad_id,
                    last_observed_at=snap.last_observed_at.isoformat()
                    if snap.last_observed_at
                    else None,
                )
            )
            if len(recent) >= 3:
                break
        result.append(
            NamingPatternGroupSchema(
                prefix=prefix,
                offer_code=offer_code,
                offer_name=offers_map.get(offer_code or "", None),
                max_number=max_number,
                total_count=len(items),
                recent_ads=recent,
            )
        )

    result.sort(key=lambda g: (g.offer_code or "", g.prefix))
    return result


async def _load_offers_map(db: AsyncSession) -> dict[str, str]:
    """Загружает маппинг offer_code -> offer_name из БД."""
    rows = await db.execute(select(Offer.code, Offer.name))
    return {code: name for code, name in rows.all()}


@router.get(
    "/naming-tracker/patterns",
    response_model=NamingTrackerResponseSchema,
    summary="Паттерны нейминга объявлений",
)
async def get_naming_patterns(
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="За сколько дней смотреть"),
    offer_code: str | None = Query(None, description="Фильтр по коду оффера"),
) -> NamingTrackerResponseSchema:
    """Возвращает группы объявлений по паттернам нейминга с макс. номером."""
    cutoff = func.now() - timedelta(days=days)
    stmt = select(AdSnapshot).where(AdSnapshot.last_observed_at >= cutoff)

    if offer_code:
        stmt = stmt.where(func.lower(AdSnapshot.resolved_offer_code) == offer_code.lower())

    result = await db.execute(stmt)
    snapshots = list(result.scalars().all())

    offers_map = await _load_offers_map(db)
    groups = _build_pattern_groups(snapshots, offers_map)

    return NamingTrackerResponseSchema(
        patterns=groups,
        total_patterns=len(groups),
    )
