# -*- coding: utf-8 -*-
"""Резолв явного scan set кабинетов для observer.

Scan set = объединение offer_ad_accounts всех АКТИВНЫХ офферов:
кабинет сканируется, если привязан хотя бы к одному активному офферу.
Пустой scan set всегда останавливает цикл fail-closed: текущая вкладка браузера
никогда не используется как неявная account identity.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.ad_account_catalog import ad_account_catalog
from core.meta_api.identity import require_ad_account_id


def allowlist_blocks_scan(single_cabinet: bool, campaign_ids: list[str]) -> bool:
    """Opt-in мониторинг: при ОДНОМ кабинете пустой allowlist = ничего не отслеживаем.

    Money-критично: раньше пустой campaign_ids означал «сканировать все мои кампании»
    (owner_tag-резолв в browser-agent). Теперь пусто = НИЧЕГО (скан не гоняем, авто-стоп
    не работает). При мульти-кабе (>1 кабинета) allowlist неприменим (campaign.id не
    уникальны меж кабинетами) → не блокируем, скоупинг через owner_tag.

    Single source: используется и observer'ом, и operator snapshot при вычислении
    `scan_blocked_reason`, чтобы UI-причина совпадала с реальным поведением actor.
    """
    return single_cabinet and not campaign_ids


def normalize_account_id(raw: str | None) -> str | None:
    """Нормализует ID кабинета: трим, срез префикса act_, проверка «только цифры».

    Возвращает числовую строку или None, если значение невалидно.
    """
    try:
        return require_ad_account_id(raw)
    except ValueError:
        return None


async def resolve_scan_account_ids(engine: AsyncEngine) -> list[str]:
    """Sorted DISTINCT union of accounts linked to active offers."""

    async with engine.connect() as conn:
        return await ad_account_catalog.resolve_scan_set(conn)


def nothing_monitored_reason_for(
    account_ids: Sequence[str],
    campaign_ids: Sequence[str],
) -> str | None:
    """Чистая проверка «скан включён, но не покрывает ни одного объявления».

    Вынесена из async-обёртки, чтобы вызывающий с уже готовым набором кабинетов
    (снимок оператора) не делал повторный запрос в БД, а тексты и условие
    оставались в одном месте.
    """
    if not account_ids:
        return "Нет активных офферов с кабинетами — сканировать нечего."
    if allowlist_blocks_scan(len(account_ids) <= 1, list(campaign_ids or [])):
        return "Список кампаний пуст — выберите кампании для мониторинга на странице «Кампании»."
    return None


async def scan_nothing_monitored_reason(engine: AsyncEngine, campaign_ids: list[str]) -> str | None:
    """Почему включённый скан фактически НИЧЕГО не отслеживает (или None если всё ок).

    НЕ учитывает is_scanning_enabled — это решает caller:
      - дашборд (scan_blocked_reason) зовёт только при включённом скане (баннер-подстраховка);
      - гейт включения (PATCH /settings/observer/scanning) зовёт всегда — чтобы не дать
        включить скан вхолостую (он бы крутился раз в интервал и ничего не отслеживал).
    Та же логика, что в реальном цикле observer (allowlist_blocks_scan) — UI совпадает с поведением.
    """
    account_ids = await resolve_scan_account_ids(engine)
    return nothing_monitored_reason_for(account_ids, campaign_ids)


async def load_ad_account_id_for_fb_ad(engine: AsyncEngine, fb_ad_id: str) -> str | None:
    """Кабинет объявления из каталога: fb_ads → fb_adsets → fb_campaigns.ad_account_id.

    Для ручных mutation-путей (TG inline, API): observer уже записал привязку при скане.
    None означает, что каталог не содержит безопасной identity; вызывающий код
    обязан отклонить money-команду и не ставить её в очередь.
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
    """Active offers without membership rows, for the operator warning."""

    async with engine.connect() as conn:
        return await ad_account_catalog.list_active_offers_without_accounts(conn)
