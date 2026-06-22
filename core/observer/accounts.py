# -*- coding: utf-8 -*-
"""Резолв scan set'а кабинетов для observer (MULTI_CABINET_PLAN.md §2.4).

Scan set = объединение offers.ad_account_ids всех АКТИВНЫХ офферов:
кабинет сканируется, если привязан хотя бы к одному активному офферу.
Пустой scan set — легитимный fallback на старое поведение (скан текущей вкладки),
решение об этом принимает observer_worker, не эта функция.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Числовой ID кабинета без префикса act_ (валидация на входе из БД — защита от мусора,
# попавшего мимо API-валидации, например ручным UPDATE).
_ACCOUNT_ID_RE = re.compile(r"^\d+$")


def allowlist_blocks_scan(single_cabinet: bool, campaign_ids: list[str]) -> bool:
    """Opt-in мониторинг: при ОДНОМ кабинете пустой allowlist = ничего не отслеживаем.

    Money-критично: раньше пустой campaign_ids означал «сканировать все мои кампании»
    (owner_tag-резолв в browser-agent). Теперь пусто = НИЧЕГО (скан не гоняем, авто-стоп
    не работает). При мульти-кабе (>1 кабинета) allowlist неприменим (campaign.id не
    уникальны меж кабинетами) → не блокируем, скоупинг через owner_tag.

    Single source: используется и observer'ом (apps/observer_worker), и API-дашбордом
    (apps/api/.../dashboard_stats) для вычисления `scan_blocked_reason` — чтобы UI-баннер
    «скан не работает: список кампаний пуст» совпадал с реальным поведением observer.
    """
    return single_cabinet and not campaign_ids


def normalize_account_id(raw: str | None) -> str | None:
    """Нормализует ID кабинета: трим, срез префикса act_, проверка «только цифры».

    Возвращает числовую строку или None, если значение невалидно.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if s.lower().startswith("act_"):
        s = s[4:]
    return s if _ACCOUNT_ID_RE.match(s) else None


async def resolve_scan_account_ids(engine: AsyncEngine) -> list[str]:
    """DISTINCT union ad_account_ids активных офферов, отсортированный для стабильного
    порядка обхода (одинаковый порядок между циклами — предсказуемые латентности).

    Невалидные значения молча отбрасываются (нормализация через normalize_account_id).
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT DISTINCT unnest(ad_account_ids) AS acc
                    FROM offers
                    WHERE is_active = TRUE
                    """
                )
            )
        ).fetchall()
    normalized = {normalize_account_id(r[0]) for r in rows}
    return sorted(acc for acc in normalized if acc)


async def load_ad_account_id_for_fb_ad(engine: AsyncEngine, fb_ad_id: str) -> str | None:
    """Кабинет объявления из каталога: fb_ads → fb_adsets → fb_campaigns.ad_account_id.

    Для ручных mutation-путей (TG inline, API): observer уже записал привязку при скане.
    None — каталог ещё без привязки (исторические данные) → mutation уйдёт с
    legacy primary-вкладки (токен общий, по ad_id сработает корректно).
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT c.ad_account_id
                    FROM fb_ads a
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE a.fb_ad_id = :fbid
                    """
                ),
                {"fbid": fb_ad_id},
            )
        ).first()
    return row[0] if row and row[0] else None


async def list_offers_without_accounts(engine: AsyncEngine) -> list[str]:
    """Коды активных офферов с пустым ad_account_ids — для warning'а в TG ops-топик:
    такие офферы выпадают из мульти-кабинетного скана, пока кабинеты не заполнены.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT code FROM offers
                    WHERE is_active = TRUE
                      AND (ad_account_ids IS NULL OR cardinality(ad_account_ids) = 0)
                    ORDER BY code
                    """
                )
            )
        ).fetchall()
    return [r[0] for r in rows]
