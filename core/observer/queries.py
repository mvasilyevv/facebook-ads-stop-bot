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
    """Пороговый конфиг оффера — только поля, которые реально читает evaluator.

    Аудит 2026-07-12 (H-3): поля cpm/ctr/funnel_ratio/spend_no_event удалены —
    загружались, но нигде не потреблялись (иллюзия защиты). Колонки в offer_rules
    оставлены: подключение этих правил — отдельной задачей, если понадобятся.
    """

    offer_id: uuid.UUID
    code: str
    name: str
    cpa_threshold: Decimal | None
    frequency_threshold: Decimal | None
    currency: str | None = None
    # Чувствительность per-offer. NULL означает отсутствие подтверждённых правил;
    # pipeline в этом случае fail-closed и не принимает автоматическое решение.
    stop_percent_of_rule: Decimal | None = None
    warning_percent_of_stop: Decimal | None = None


@dataclass(frozen=True)
class AdAlertSnapshot:
    """Текущее FSM-состояние для одного fb_ad'а."""

    ad_id: uuid.UUID
    fb_ad_id: str
    alert_state: str  # normal / warning_sent / stop_sent / claimed / disabled
    current_stage: str | None
    open_state_token: uuid.UUID | None
    snoozed_until: datetime | None
    enable_grace_until: datetime | None = None
    enable_grace_spend_cap: Decimal | None = None
    enable_grace_baseline_spend: Decimal | None = None
    enable_grace_cabinet_day_start: datetime | None = None
    enable_grace_currency: str | None = None
    enable_grace_currency_exponent: int | None = None


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
                        r.cpa_threshold,
                        r.currency,
                        r.frequency_threshold,
                        r.stop_percent_of_rule,
                        r.warning_percent_of_stop
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
            cpa_threshold=row[3],
            currency=str(row[4]) if row[4] else None,
            frequency_threshold=row[5],
            stop_percent_of_rule=row[6],
            warning_percent_of_stop=row[7],
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
                        s.snoozed_until,
                        s.enable_grace_until,
                        s.enable_grace_spend_cap,
                        s.enable_grace_baseline_spend,
                        s.enable_grace_cabinet_day_start,
                        s.enable_grace_currency,
                        s.enable_grace_currency_exponent
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
            enable_grace_until=row[6],
            enable_grace_spend_cap=row[7],
            enable_grace_baseline_spend=row[8],
            enable_grace_cabinet_day_start=row[9],
            enable_grace_currency=row[10],
            enable_grace_currency_exponent=row[11],
        )
        for row in rows
    }


async def load_observer_config(engine: AsyncEngine) -> dict[str, object] | None:
    """Singleton observer_config — интервал, флаги и operator scope.

    Возвращает None если строки нет (что не должно случаться после apply_schema).
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                        interval_seconds, is_scanning_enabled,
                        owner_campaign_tag,
                        campaign_ids,
                        am_columns_qs
                    FROM observer_config WHERE singleton_key = 'default'
                    """
                )
            )
        ).first()
    if not row:
        return None
    return {
        "interval_seconds": int(row[0]),
        "is_scanning_enabled": bool(row[1]),
        "owner_campaign_tag": row[2],
        "campaign_ids": list(row[3]) if row[3] else [],
        "am_columns_qs": row[4],
    }


async def load_scanning_enabled(engine: AsyncEngine) -> bool:
    """Единая точка чтения «глобального стопа» observer_config.is_scanning_enabled.

    Лёгкий одиночный SELECT — для воркеров, которые должны замирать на паузе
    сканирования (асимметричный стоп: пауза глушит активирующие mutations; но
    РАЗРЕШАЕТ выключающие — auto-stop/ручной pause, они снижают риск открута).

    Нет строки observer_config → False (fail-safe: чистая установка и гонка первого
    старта никогда не включают сканирование до явного действия оператора).
    Ошибку соединения НЕ глушит — пробрасывает наверх. Все caller'ы крутятся в цикле
    с try/except, который трактует исключение как «пропустить тик».
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT is_scanning_enabled FROM observer_config "
                    "WHERE singleton_key = 'default'"
                )
            )
        ).first()
    if row is None:
        return False
    return bool(row[0])


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


def parse_owner_tags(raw: str | None) -> list[str]:
    """Разбирает CSV-строку owner-тегов в список непустых тегов.

    Разделители — запятая/точка-с-запятой. "MV,ABC" → ["MV", "ABC"].
    Пустая строка/None → [] (фильтр выключен).
    """
    if not raw:
        return []
    return [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]


def multi_cabinet_requires_owner_tag(account_count: int, owner_tag: str | None) -> bool:
    """Money-гард: при мульти-кабе (>1 кабинета) без owner_tag скан небезопасен.

    В мульти-кабинете глобальный allowlist campaign_ids игнорируется (campaign.id не
    уникальны меж кабинетами) — скоупинг полностью полагается на owner_tag. Если тег
    пуст/None, campaign_matches_owner → True для ВСЕХ кампаний, и в shared-кабинете бот
    оценил бы стоп-правила и создал бы pause_ad по ЧУЖИМ объявлениям (необратимо, чужие
    деньги). Зеркалит single-cab guard allowlist_blocks_scan: при пустом скоупе скан не
    гоняем. True → скан этого набора кабинетов надо ПРОПУСТИТЬ ради безопасности.
    """
    return account_count > 1 and not parse_owner_tags(owner_tag)


def campaign_matches_owner(
    *,
    campaign_name: str,
    ad_name: str,
    owner_tag: str | None,
) -> bool:
    """True если кампания/объявление принадлежит владельцу (owner-scoping).

    В общем рекламном кабинете отсекает чужие кампании. Поддерживает НЕСКОЛЬКО тегов
    через запятую ("MV,ABC") — совпадение с ЛЮБЫМ. Тег ищется word-boundary (как код
    оффера), case-insensitive, в campaign_name ИЛИ ad_name. Так 'MV' матчит
    'MV | GH | CR2', но НЕ 'MZ Artemteam' (граница слова) и НЕ внутри слова.
    Пустой/None owner_tag → True (фильтр выключен, обрабатываются все кампании).
    """
    tags = parse_owner_tags(owner_tag)
    if not tags:
        return True
    hay_campaign = (campaign_name or "").casefold()
    hay_ad = (ad_name or "").casefold()
    for tag in tags:
        pat = _owner_tag_pattern(tag.casefold())
        if pat.search(hay_campaign) or pat.search(hay_ad):
            return True
    return False
