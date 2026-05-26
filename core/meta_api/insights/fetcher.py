# -*- coding: utf-8 -*-
"""Высокоуровневые методы для чтения метрик через Marketing API.

Оборачивает MetaApiHighLevelClient.get_insights() и возвращает уже
распарсенные MetaInsightsRow / ScannedAdRow вместо сырых dict.

Запрещённые импорты (META_INTEGRATION_PLAN.md §3.3):
    - core.observer.* — смешивание контрактов
    - apps.observer_worker.*, apps.disable_worker.*, apps.enable_worker.*
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.meta_api.adapters import meta_insights_row_to_scanned_ad_row, parse_insights_row_from_dict
from core.meta_api.client import MetaApiHighLevelClient
from core.meta_api.schemas import MetaInsightsRow
from core.models import FbAdset, FbCampaign, ObserverSettings, Offer
from core.scanner.models import ScannedAdRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class InsightsFetcher:
    """Высокоуровневая обёртка над MetaApiHighLevelClient.get_insights().

    Превращает raw dicts от Marketing API в типизированные MetaInsightsRow,
    с поддержкой пагинации, фильтрации по офферу, агрегации.
    """

    def __init__(self, client: MetaApiHighLevelClient) -> None:
        self._client = client

    # ── Основные методы ─────────────────────────────────────────────────────

    async def fetch_for_ad_account(
        self,
        ad_account_id: str,
        *,
        level: str = "ad",
        date_preset: str = "today",
        time_range: dict | None = None,
        breakdowns: list[str] | None = None,
        limit: int = 500,
        initiated_by: str = "fetcher",
    ) -> list[MetaInsightsRow]:
        """Получить insights для всего ad_account, парсит в MetaInsightsRow.

        Args:
            ad_account_id: ID рекламного кабинета (с "act_" или без)
            level: уровень агрегации — "ad", "adset", "campaign", "account"
            date_preset: предустановленный период ("today", "yesterday", "last_7d", ...)
            time_range: явный диапазон {"since": "YYYY-MM-DD", "until": "YYYY-MM-DD"}
            breakdowns: разбивки (["age", "gender", "country", ...])
            limit: максимальное количество строк в ответе
            initiated_by: строка аудита

        Returns:
            Список MetaInsightsRow, отсортированный как в ответе API.
        """
        raw_rows = await self._client.get_insights(
            ad_account_id,
            level=level,
            date_preset=date_preset,
            time_range=time_range,
            breakdowns=breakdowns,
            limit=limit,
        )
        return [parse_insights_row_from_dict(row) for row in raw_rows]

    async def fetch_for_ads(
        self,
        ad_account_id: str,
        ad_ids: list[str],
        *,
        date_preset: str = "today",
        limit: int = 500,
        initiated_by: str = "fetcher",
    ) -> list[MetaInsightsRow]:
        """Получить insights для конкретных ad_id (filtering='ad.id IN [...]').

        Args:
            ad_account_id: ID рекламного кабинета
            ad_ids: список fb_ad_id для фильтрации
            date_preset: предустановленный период
            limit: максимальное количество строк
            initiated_by: строка аудита

        Returns:
            Список MetaInsightsRow только для запрошенных объявлений.
        """
        if not ad_ids:
            return []

        # Фильтр Meta API: ad.id IN ['id1', 'id2', ...]
        filtering = [{"field": "ad.id", "operator": "IN", "value": ad_ids}]

        raw_rows = await self._client.get_insights(
            ad_account_id,
            level="ad",
            date_preset=date_preset,
            filtering=filtering,
            limit=limit,
        )
        return [parse_insights_row_from_dict(row) for row in raw_rows]

    async def fetch_for_campaigns(
        self,
        ad_account_id: str,
        campaign_ids: list[str],
        *,
        level: str = "campaign",
        date_preset: str = "today",
        initiated_by: str = "fetcher",
    ) -> list[MetaInsightsRow]:
        """Получить insights для конкретных campaign_id.

        Args:
            ad_account_id: ID рекламного кабинета
            campaign_ids: список campaign_id для фильтрации
            level: уровень агрегации ("campaign", "adset", "ad")
            date_preset: предустановленный период
            initiated_by: строка аудита

        Returns:
            Список MetaInsightsRow для запрошенных кампаний.
        """
        if not campaign_ids:
            return []

        # Фильтр по campaign_id
        filtering = [{"field": "campaign.id", "operator": "IN", "value": campaign_ids}]

        raw_rows = await self._client.get_insights(
            ad_account_id,
            level=level,
            date_preset=date_preset,
            filtering=filtering,
        )
        return [parse_insights_row_from_dict(row) for row in raw_rows]

    async def fetch_for_offer(
        self,
        db: AsyncSession,
        offer_code: str,
        *,
        ad_account_id: str | None = None,
        date_preset: str = "today",
        initiated_by: str = "fetcher",
    ) -> list[MetaInsightsRow]:
        """Получить insights для всех объявлений конкретного оффера.

        Алгоритм:
        1. Найти все fb_ad_id связанные с оффером через:
           Offer → FbCampaign → FbAdset → FbAd
        2. Если ad_account_id не передан — прочитать из ObserverSettings.fb_account_id
        3. Вызвать fetch_for_ads с найденными ID

        Args:
            db: асинхронная SQLAlchemy-сессия
            offer_code: код оффера (например "DRC_CR2")
            ad_account_id: ID рекламного кабинета; если None — берётся из настроек
            date_preset: предустановленный период
            initiated_by: строка аудита

        Returns:
            Список MetaInsightsRow для объявлений оффера.
        """
        # Получаем ad_account_id из ObserverSettings если не передан явно
        if ad_account_id is None:
            ad_account_id = await _get_fb_account_id(db)

        # Ищем все fb_ad_id для оффера через иерархию кампаний
        fb_ad_ids = await _load_fb_ad_ids_for_offer(db, offer_code)

        if not fb_ad_ids:
            logger.info(
                "fetch_for_offer: оффер %r — объявления не найдены в БД",
                offer_code,
            )
            return []

        logger.debug(
            "fetch_for_offer: оффер %r → %d объявлений, account=%s",
            offer_code,
            len(fb_ad_ids),
            ad_account_id,
        )

        return await self.fetch_for_ads(
            ad_account_id,
            fb_ad_ids,
            date_preset=date_preset,
            initiated_by=initiated_by,
        )

    async def fetch_as_scanned_rows(
        self,
        ad_account_id: str,
        *,
        date_preset: str = "today",
        initiated_by: str = "fetcher",
    ) -> list[ScannedAdRow]:
        """Получить insights и сразу конвертировать в ScannedAdRow для evaluator/rules.

        delivery_status по умолчанию "active" — true delivery_status требует
        отдельного запроса к /{ad_id}?fields=effective_status (Этап 5).

        Args:
            ad_account_id: ID рекламного кабинета
            date_preset: предустановленный период
            initiated_by: строка аудита

        Returns:
            Список ScannedAdRow готовых для передачи в evaluator.
        """
        rows = await self.fetch_for_ad_account(
            ad_account_id,
            date_preset=date_preset,
            initiated_by=initiated_by,
        )
        return [meta_insights_row_to_scanned_ad_row(row) for row in rows]


# ── Утилитные функции ────────────────────────────────────────────────────────


async def fetch_ad_account_summary(
    client: MetaApiHighLevelClient,
    ad_account_id: str,
    *,
    date_preset: str = "today",
) -> dict[str, Decimal | int]:
    """Агрегаты по всему ad account за период.

    Суммирует метрики всех объявлений в кабинете за указанный период.

    Args:
        client: экземпляр MetaApiHighLevelClient
        ad_account_id: ID рекламного кабинета
        date_preset: предустановленный период

    Returns:
        Словарь с агрегатами:
        {
            "total_spend": Decimal,
            "total_impressions": int,
            "total_clicks": int,
            "total_leads": int,
            "total_registrations": int,
            "total_deposits": int,
            "total_reach": int,
        }
    """
    fetcher = InsightsFetcher(client)
    rows = await fetcher.fetch_for_ad_account(
        ad_account_id,
        date_preset=date_preset,
        initiated_by="fetch_ad_account_summary",
    )

    total_spend = Decimal("0")
    total_impressions = 0
    total_clicks = 0
    total_leads = 0
    total_registrations = 0
    total_deposits = 0
    total_reach = 0

    for row in rows:
        total_spend += row.spend
        total_impressions += row.impressions
        total_clicks += row.clicks
        total_leads += row.leads
        total_registrations += row.registrations
        total_deposits += row.deposits
        total_reach += row.reach or 0

    return {
        "total_spend": total_spend,
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "total_leads": total_leads,
        "total_registrations": total_registrations,
        "total_deposits": total_deposits,
        "total_reach": total_reach,
    }


async def fetch_breakdown_by_country(
    client: MetaApiHighLevelClient,
    ad_account_id: str,
    *,
    date_preset: str = "last_7d",
) -> list[dict]:
    """GET /insights с breakdowns=['country'].

    Возвращает сырые dict — breakdown строки имеют разную структуру
    в зависимости от комбинации breakdowns, их лучше не маппить в MetaInsightsRow.

    Args:
        client: экземпляр MetaApiHighLevelClient
        ad_account_id: ID рекламного кабинета
        date_preset: предустановленный период (по умолчанию "last_7d")

    Returns:
        Список dict с полями insights + поле "country".
    """
    raw_rows = await client.get_insights(
        ad_account_id,
        level="ad",
        date_preset=date_preset,
        breakdowns=["country"],
        limit=1000,
    )
    return raw_rows


# ── Внутренние вспомогательные функции ──────────────────────────────────────


async def _get_fb_account_id(db: AsyncSession) -> str:
    """Прочитать fb_account_id из ObserverSettings (singleton).

    Raises:
        ValueError: если ObserverSettings отсутствуют или fb_account_id не задан.
    """
    result = await db.execute(
        select(ObserverSettings.fb_account_id).where(ObserverSettings.singleton_key == "default")
    )
    fb_account_id: str | None = result.scalar_one_or_none()

    if not fb_account_id:
        raise ValueError(
            "ObserverSettings.fb_account_id не задан. "
            "Укажите ID рекламного кабинета в настройках или передайте ad_account_id явно."
        )
    return fb_account_id


async def _load_fb_ad_ids_for_offer(db: AsyncSession, offer_code: str) -> list[str]:
    """Загрузить все fb_ad_id объявлений принадлежащих офферу.

    Маршрут: Offer.code → FbCampaign.offer_id → FbAdset.campaign_id → FbAd.adset_id

    Args:
        db: асинхронная SQLAlchemy-сессия
        offer_code: код оффера

    Returns:
        Список fb_ad_id (строк). Пустой список если оффер не найден или нет объявлений.
    """
    # Загружаем Offer вместе с иерархией кампаний одним запросом
    result = await db.execute(
        select(Offer)
        .where(Offer.code == offer_code)
        .options(
            selectinload(Offer.campaigns).selectinload(FbCampaign.adsets).selectinload(FbAdset.ads)
        )
    )
    offer: Offer | None = result.scalar_one_or_none()

    if offer is None:
        logger.warning("_load_fb_ad_ids_for_offer: оффер %r не найден", offer_code)
        return []

    fb_ad_ids: list[str] = []
    for campaign in offer.campaigns:
        for adset in campaign.adsets:
            for ad in adset.ads:
                fb_ad_ids.append(ad.fb_ad_id)

    return fb_ad_ids
