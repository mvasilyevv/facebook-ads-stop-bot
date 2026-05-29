# -*- coding: utf-8 -*-
"""Чтение из БД для observer: offers + правила + текущие FSM-состояния.

Без ORM-моделей — raw SQL через AsyncEngine. Это снижает coupling и позволяет
быстро менять схему запросов без рефакторинга десятков ORM-классов.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class OfferRules:
    """Конфиг 6 стоп-правил для одного оффера."""

    offer_id: uuid.UUID
    code: str
    name: str
    spend_no_event_threshold: Decimal | None
    cpa_threshold: Decimal | None
    cpm_threshold: Decimal | None
    ctr_threshold: Decimal | None
    frequency_threshold: Decimal | None
    funnel_ratio_threshold: Decimal | None


@dataclass(frozen=True)
class AdAlertSnapshot:
    """Текущее FSM-состояние для одного fb_ad'а."""

    ad_id: uuid.UUID
    fb_ad_id: str
    alert_state: str  # normal / warning_sent / stop_sent / claimed / disabled
    current_stage: str | None
    open_state_token: uuid.UUID | None
    snoozed_until: datetime | None


async def load_active_offers(engine: AsyncEngine) -> list[OfferRules]:
    """Загружает все активные офферы вместе с конфигурацией правил.

    Только is_active=true — выключенные офферы не должны участвовать в матчинге.
    LEFT JOIN потому что у нового оффера ещё может не быть offer_rules.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT
                        o.id, o.code, o.name,
                        r.spend_no_event_threshold,
                        r.cpa_threshold,
                        r.cpm_threshold,
                        r.ctr_threshold,
                        r.frequency_threshold,
                        r.funnel_ratio_threshold
                    FROM offers o
                    LEFT JOIN offer_rules r ON r.offer_id = o.id
                    WHERE o.is_active = TRUE
                    ORDER BY length(o.code) DESC, o.code
                    """
                )
            )
        ).all()
    return [
        OfferRules(
            offer_id=row[0],
            code=str(row[1]),
            name=str(row[2]),
            spend_no_event_threshold=row[3],
            cpa_threshold=row[4],
            cpm_threshold=row[5],
            ctr_threshold=row[6],
            frequency_threshold=row[7],
            funnel_ratio_threshold=row[8],
        )
        for row in rows
    ]


async def load_alert_state_by_fb_ad_id(
    engine: AsyncEngine,
    *,
    fb_ad_ids: list[str],
) -> dict[str, AdAlertSnapshot]:
    """Возвращает map fb_ad_id → FSM-состояние для подмножества объявлений.

    Если для fb_ad_id нет записи в ad_alert_state — он не попадёт в результат,
    caller интерпретирует это как новое объявление с состоянием 'normal'.
    """
    if not fb_ad_ids:
        return {}

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT
                        a.id, a.fb_ad_id,
                        s.alert_state, s.current_stage, s.open_state_token,
                        s.snoozed_until
                    FROM fb_ads a
                    JOIN ad_alert_state s ON s.ad_id = a.id
                    WHERE a.fb_ad_id = ANY(:ids)
                    """
                ),
                {"ids": list(fb_ad_ids)},
            )
        ).all()

    return {
        row[1]: AdAlertSnapshot(
            ad_id=row[0],
            fb_ad_id=str(row[1]),
            alert_state=str(row[2]),
            current_stage=row[3],
            open_state_token=row[4],
            snoozed_until=row[5],
        )
        for row in rows
    }


async def load_observer_config(engine: AsyncEngine) -> dict[str, object] | None:
    """Singleton observer_config — интервал, флаги, install_cost.

    Возвращает None если строки нет (что не должно случаться после apply_schema).
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                        interval_seconds, jitter_seconds,
                        stale_data_threshold_seconds, install_cost_usd,
                        agent_commission_percent, is_scanning_enabled,
                        owner_campaign_tag
                    FROM observer_config WHERE singleton_key = 'default'
                    """
                )
            )
        ).first()
    if not row:
        return None
    return {
        "interval_seconds": int(row[0]),
        "jitter_seconds": int(row[1]),
        "stale_data_threshold_seconds": int(row[2]),
        "install_cost_usd": row[3],
        "agent_commission_percent": row[4],
        "is_scanning_enabled": bool(row[5]),
        "owner_campaign_tag": row[6],
    }


@lru_cache(maxsize=1024)
def _offer_code_pattern(code_lower: str) -> re.Pattern[str]:
    """Word-boundary regex для кода оффера.

    Используем '[a-z0-9]' как границу слова (не \\b, потому что подчёркивание
    считается частью слова в \\b). Это позволяет 'KE_CR2' матчиться в
    'CR2_KE_FOO', но не в 'KECR2'.
    """
    return re.compile(r"(?<![a-z0-9])" + re.escape(code_lower) + r"(?![a-z0-9])")


def match_offer_for_ad(
    *,
    campaign_name: str,
    ad_name: str,
    offers: list[OfferRules],
) -> OfferRules | None:
    """Матчит оффер по вхождению кода в название (word-boundary).

    Контракт (из CLAUDE.md):
    - Приоритет источника: ad_name выигрывает у campaign_name.
    - Из совпадающих кодов выигрывает самый длинный.
    - При равной длине — алфавитно первый код (ASCENDING). Это технический
      tie-breaker: даёт детерминированный результат для повторных запусков,
      но НЕ гарантия что это семантически правильный оффер. Менеджер должен
      использовать уникальные substring'и в названиях кампаний; равная длина
      — это уже коллизия конфигурации, мы только не позволяем ей отдавать
      разный результат от вызова к вызову.
    - Case-insensitive. Word-boundary исключает 'KE_CR2' внутри 'KEN_CR2_FOO'.
    """
    if not offers:
        return None

    def _best_match_in(text: str) -> OfferRules | None:
        text_lower = (text or "").casefold()
        best: OfferRules | None = None
        for offer in offers:
            code_lower = offer.code.casefold()
            if not code_lower:
                continue
            if not _offer_code_pattern(code_lower).search(text_lower):
                continue
            if best is None:
                best = offer
                continue
            # Сначала длина: длиннее → специфичнее → выигрывает.
            if len(offer.code) > len(best.code):
                best = offer
            elif len(offer.code) == len(best.code) and offer.code < best.code:
                # При равной длине — алфавитно первый (стабильный tie-breaker).
                best = offer
        return best

    matched = _best_match_in(ad_name)
    if matched is not None:
        return matched
    return _best_match_in(campaign_name)


@lru_cache(maxsize=256)
def _owner_tag_pattern(tag_lower: str) -> re.Pattern[str]:
    """Word-boundary regex для owner-тега (логика как у _offer_code_pattern)."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(tag_lower) + r"(?![a-z0-9])")


def campaign_matches_owner(
    *,
    campaign_name: str,
    ad_name: str,
    owner_tag: str | None,
) -> bool:
    """True если кампания/объявление принадлежит владельцу (owner-scoping).

    В общем рекламном кабинете отсекает чужие кампании. Тег ищется word-boundary
    (как код оффера), case-insensitive, в campaign_name ИЛИ ad_name. Так 'MV'
    матчит 'MV | GH | CR2', но НЕ 'MZ Artemteam' (граница слова) и НЕ внутри слова.
    Пустой/None owner_tag → True (фильтр выключен, обрабатываются все кампании —
    обратная совместимость для кабинетов с одним владельцем).
    """
    if not owner_tag or not owner_tag.strip():
        return True
    pat = _owner_tag_pattern(owner_tag.strip().casefold())
    return bool(
        pat.search((campaign_name or "").casefold()) or pat.search((ad_name or "").casefold())
    )
