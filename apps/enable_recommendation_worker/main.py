# -*- coding: utf-8 -*-
"""Воркер рекомендаций на включение OFF-объявлений."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from core.db import get_session_factory
from core.enable_recommendations.service import (
    attach_recommendation_telegram_delivery,
    cleanup_orphaned_recommendation_events,
    collect_enable_recommendation_candidates,
    persist_enable_recommendation_candidates,
)
from core.models import AdSnapshot, FbAd, FbAdset
from core.telegram.delivery import broadcast_enable_recommendation_message
from core.telegram.renderer import normalize_enable_recommendation_reason

logger = logging.getLogger(__name__)

RECOMMENDATION_POLL_INTERVAL_SECONDS = 30


async def process_enable_recommendation_cycle() -> int:
    """Выполняет один цикл поиска и публикации новых рекомендаций."""
    factory = get_session_factory()
    async with factory() as session:
        live_batch_started_at, candidates = await collect_enable_recommendation_candidates(session)
        if live_batch_started_at is None or not candidates:
            return 0

        created_events = await persist_enable_recommendation_candidates(session, candidates)
        await session.commit()

    snapshot_by_ad: dict[str, AdSnapshot] = {}
    if created_events:
        async with factory() as session:
            result = await session.execute(
                select(AdSnapshot)
                .options(
                    joinedload(AdSnapshot.fb_ad).joinedload(FbAd.adset).joinedload(FbAdset.campaign)
                )
                .where(AdSnapshot.fb_ad_id.in_([event.fb_ad_id for event in created_events]))
            )
            snapshot_by_ad = {
                snapshot.fb_ad_id: snapshot for snapshot in result.scalars().unique().all()
            }

    delivered_count = 0
    for event in created_events:
        snapshot = snapshot_by_ad.get(event.fb_ad_id)
        reason_title, reason_text = normalize_enable_recommendation_reason(
            recommendation_level=event.recommendation_level,
            reason_title=event.reason_title,
            reason_text=event.reason_text,
        )
        # Получаем имена кампании и адсета через цепочку JOIN
        campaign_name: str | None = None
        adset_name: str | None = None
        if snapshot is not None and snapshot.fb_ad is not None:
            fb_ad = snapshot.fb_ad
            if fb_ad.adset is not None:
                adset_name = fb_ad.adset.adset_name
                if fb_ad.adset.campaign is not None:
                    campaign_name = fb_ad.adset.campaign.campaign_name

        refs = await broadcast_enable_recommendation_message(
            event_id=event.id,
            ad_name=event.ad_name,
            fb_ad_id=event.fb_ad_id,
            campaign_name=campaign_name,
            adset_name=adset_name,
            delivery_status=event.delivery_status,
            recommendation_level=event.recommendation_level.value,
            matched_rule_codes=event.matched_rule_codes or [],
            reason_title=reason_title,
            reason_text=reason_text,
            metrics_json=event.metrics_json or {},
        )
        if refs:
            async with factory() as session:
                first_chat_id, first_message_id = refs[0]
                await attach_recommendation_telegram_delivery(
                    session,
                    event_id=event.id,
                    chat_id=first_chat_id,
                    message_id=first_message_id,
                )
                await session.commit()
        delivered_count += 1

    # Очистка orphaned events (раз в цикл, ошибки глотаем)
    try:
        async with factory() as session:
            await cleanup_orphaned_recommendation_events(session)
            await session.commit()
    except Exception:
        logger.debug("Ошибка при очистке orphaned recommendation events", exc_info=True)

    return delivered_count


async def recommendation_worker_loop(
    *,
    poll_interval_seconds: int = RECOMMENDATION_POLL_INTERVAL_SECONDS,
    shutdown_event: asyncio.Event | None = None,
    process_cycle=None,
) -> None:
    """Бесконечный цикл recommendation worker."""
    process_cycle = process_cycle or process_enable_recommendation_cycle
    while not (shutdown_event and shutdown_event.is_set()):
        try:
            created_count = await process_cycle()
            if created_count:
                logger.info(
                    "Recommendation worker: опубликовано новых рекомендаций: %s",
                    created_count,
                )
        except Exception:
            logger.exception("Recommendation worker: ошибка в цикле")

        try:
            if shutdown_event:
                await asyncio.wait_for(shutdown_event.wait(), timeout=poll_interval_seconds)
                break
        except asyncio.TimeoutError:
            continue
