# -*- coding: utf-8 -*-
"""Воркер рекомендаций на включение OFF-объявлений."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import delete, select

from core.db import get_session_factory
from core.enable_recommendations.service import (
    attach_recommendation_telegram_delivery,
    cleanup_orphaned_recommendation_events,
    collect_enable_recommendation_candidates,
    load_pending_enable_recommendation_events,
    persist_enable_recommendation_candidates,
    promote_recommendation_to_enable_task,
)
from core.models import AdAutoEnableDisabled
from core.settings_queries import get_observer_settings
from core.telegram.delivery import (
    broadcast_enable_recommendation_message,
    broadcast_enable_task_queue_message,
)
from core.telegram.renderer import normalize_enable_recommendation_reason

logger = logging.getLogger(__name__)

RECOMMENDATION_POLL_INTERVAL_SECONDS = 30


async def _reset_stale_auto_enable_disabled(cabinet_day_started_at: object) -> None:
    """Удаляет записи AdAutoEnableDisabled, устаревшие при смене кабинетного дня."""
    if cabinet_day_started_at is None:
        return
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            delete(AdAutoEnableDisabled).where(
                AdAutoEnableDisabled.cabinet_day_started_at != cabinet_day_started_at
            )
        )
        await session.commit()


async def _load_auto_enable_disabled_set() -> set[str]:
    """Возвращает множество fb_ad_id у которых автовключение выключено вручную."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(AdAutoEnableDisabled.fb_ad_id))
        return set(result.scalars().all())


async def _auto_enable_new_events(
    created_events: list,
    disabled_set: set[str],
) -> None:
    """Автоматически создаёт EnableTask для каждого нового recommendation event."""
    factory = get_session_factory()
    for event in created_events:
        # Пропускаем объявления с выключенным автовключением
        fb_ad = getattr(event, "fb_ad", None)
        fb_ad_id = fb_ad.fb_ad_id if fb_ad else None
        if fb_ad_id and fb_ad_id in disabled_set:
            logger.debug(
                "Авто-включение пропущено (выключено вручную) для %s",
                fb_ad_id,
            )
            continue
        try:
            async with factory() as session:
                result = await promote_recommendation_to_enable_task(
                    session,
                    event_id=event.id,
                    requested_by_username="auto",
                )
                await session.commit()

            if result.outcome in ("created", "requeued") and result.fb_ad_id:
                await broadcast_enable_task_queue_message(
                    ad_name=result.ad_name or "",
                    fb_ad_id=result.fb_ad_id,
                    requested_by_username="auto",
                    created_new=result.created_new,
                    incident_key=str(event.id),
                )
                logger.info(
                    "Авто-включение: создана задача для %s (%s)",
                    result.ad_name,
                    result.fb_ad_id,
                )
            elif result.outcome not in ("existing",):
                logger.debug(
                    "Авто-включение пропущено для %s: %s",
                    result.fb_ad_id,
                    result.detail,
                )
        except Exception:
            logger.exception(
                "Ошибка авто-включения для события %s",
                event.id,
            )


async def process_enable_recommendation_cycle() -> int:
    """Выполняет один цикл поиска и публикации новых рекомендаций."""
    factory = get_session_factory()
    async with factory() as session:
        live_batch_started_at, candidates = await collect_enable_recommendation_candidates(session)
        if live_batch_started_at is None or not candidates:
            return 0

        created_events = await persist_enable_recommendation_candidates(session, candidates)
        await session.commit()

        # Читаем флаг авто-включения и cabinet_day_started_at
        obs_settings = await get_observer_settings(session)
        auto_enable = bool(obs_settings.auto_enable_recommendations) if obs_settings else False
        cabinet_day = obs_settings.cabinet_day_started_at if obs_settings else None

    # Сброс устаревших per-ad отключений при смене кабинетного дня
    try:
        await _reset_stale_auto_enable_disabled(cabinet_day)
    except Exception:
        logger.debug("Ошибка при сбросе устаревших per-ad отключений", exc_info=True)

    # Авто-включение новых рекомендаций
    if auto_enable and created_events:
        disabled_set = await _load_auto_enable_disabled_set()
        await _auto_enable_new_events(created_events, disabled_set)

    async with factory() as session:
        pending_events = await load_pending_enable_recommendation_events(
            session,
            live_batch_started_at=live_batch_started_at,
        )

    delivered_count = 0
    for event in pending_events:
        reason_title, reason_text = normalize_enable_recommendation_reason(
            recommendation_level=event.recommendation_level,
            reason_title=event.reason_title,
            reason_text=event.reason_text,
        )
        fb_ad = event.fb_ad
        if fb_ad is None:
            logger.warning(
                "Recommendation worker: событие %s пропущено — объявление не найдено",
                event.id,
            )
            continue

        # Получаем имена кампании и адсета через цепочку fb_ad → adset → campaign
        campaign_name: str | None = None
        adset_name: str | None = None
        if fb_ad.adset is not None:
            adset_name = fb_ad.adset.adset_name
            if fb_ad.adset.campaign is not None:
                campaign_name = fb_ad.adset.campaign.campaign_name

        refs = await broadcast_enable_recommendation_message(
            event_id=event.id,
            ad_name=fb_ad.ad_name,
            fb_ad_id=fb_ad.fb_ad_id,
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
