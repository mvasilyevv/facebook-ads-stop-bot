# -*- coding: utf-8 -*-
"""Owner-scoped резолв ad_id по offer-коду для массовых mutations (bulk pause/activate).

Используется TG-командами /pause /resume. Возвращает только активные объявления
СВОИХ кампаний (owner-scoping), чтобы массовая операция не задела чужую рекламу
в общем кабинете.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.queries import campaign_matches_owner

MAX_BULK = 50


async def resolve_owner_ad_ids(
    engine: AsyncEngine,
    *,
    offer_code: str,
    owner_tag: str | None = None,
    limit: int = MAX_BULK,
) -> tuple[list[str], int]:
    """Активные fb_ad_id по offer-коду (word-boundary), отфильтрованные owner-тегом.

    Owner-scoping: если owner_tag задан — оставляем только кампании/объявления,
    чьё название содержит любой owner-тег (через campaign_matches_owner). Защита
    от массового отключения чужих кампаний в общем кабинете.

    Возвращает (ad_ids[:limit], total_matched_after_owner) — второй элемент нужен,
    чтобы предупредить пользователя об усечении до limit.
    """
    escaped = re.escape(offer_code.lower())
    pattern = rf"(^|[^a-z0-9]){escaped}([^a-z0-9]|$)"
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT DISTINCT a.fb_ad_id, c.campaign_name, a.ad_name
                    FROM fb_ads a
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE (c.campaign_name ~* :pattern OR a.ad_name ~* :pattern)
                      AND a.fb_ad_id IS NOT NULL
                      AND a.is_active = TRUE
                    """
                ),
                {"pattern": pattern},
            )
        ).all()

    owned: list[str] = []
    for fb_ad_id, campaign_name, ad_name in rows:
        if not fb_ad_id:
            continue
        if not campaign_matches_owner(
            campaign_name=campaign_name or "", ad_name=ad_name or "", owner_tag=owner_tag
        ):
            continue
        owned.append(str(fb_ad_id))

    return owned[:limit], len(owned)
