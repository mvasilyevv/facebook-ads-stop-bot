# -*- coding: utf-8 -*-
"""Общие запросы для корректировки ложных депозитов."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import AdDepositCorrection, FbAd, FbAdset, FbCampaign


async def load_fake_deposits_map(db: AsyncSession) -> dict[str, int]:
    """Карта fb_ad_id → fake_count для корректировки депозитов."""
    rows = (
        await db.execute(
            select(FbAd.fb_ad_id, AdDepositCorrection.fake_count)
            .join(FbAd, FbAd.id == AdDepositCorrection.ad_id)
            .where(AdDepositCorrection.fake_count > 0)
        )
    ).all()
    return {fb_ad_id: count for fb_ad_id, count in rows}


def effective_deposits(deposits: int, fb_ad_id: str, fake_map: dict[str, int]) -> int:
    """Эффективное количество депозитов с учётом ложных."""
    return max(0, deposits - fake_map.get(fb_ad_id, 0))


async def load_total_fake_deposits(db: AsyncSession) -> int:
    """Общее количество ложных депозитов."""
    total = (
        await db.execute(
            select(func.coalesce(func.sum(AdDepositCorrection.fake_count), 0)).where(
                AdDepositCorrection.fake_count > 0
            )
        )
    ).scalar()
    return int(total or 0)


async def load_fake_deposits_by_campaign(db: AsyncSession) -> dict[str, int]:
    """Суммарное количество ложных депозитов по кампании через JOIN."""
    rows = (
        await db.execute(
            select(
                FbCampaign.campaign_name,
                func.sum(AdDepositCorrection.fake_count),
            )
            .join(FbAd, FbAd.id == AdDepositCorrection.ad_id)
            .join(FbAdset, FbAd.adset_id == FbAdset.id)
            .join(FbCampaign, FbAdset.campaign_id == FbCampaign.id)
            .where(AdDepositCorrection.fake_count > 0)
            .group_by(FbCampaign.campaign_name)
        )
    ).all()
    return {name: int(total) for name, total in rows}


async def load_fake_deposits_by_offer(db: AsyncSession) -> dict[str, int]:
    """Суммарное количество ложных депозитов по офферу через JOIN."""
    rows = (
        await db.execute(
            select(
                FbCampaign.offer_code,
                func.sum(AdDepositCorrection.fake_count),
            )
            .join(FbAd, FbAd.id == AdDepositCorrection.ad_id)
            .join(FbAdset, FbAd.adset_id == FbAdset.id)
            .join(FbCampaign, FbAdset.campaign_id == FbCampaign.id)
            .where(
                AdDepositCorrection.fake_count > 0,
                FbCampaign.offer_code.isnot(None),
            )
            .group_by(FbCampaign.offer_code)
        )
    ).all()
    return {code: int(total) for code, total in rows if code}
