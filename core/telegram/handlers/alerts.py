# -*- coding: utf-8 -*-
"""Inline-кнопки под алертами: dis (disable) и snz (snooze).

callback_data: '<action>:<fb_ad_id>:<token>' (см. renderer.render_inline_keyboard).
action ∈ {'dis', 'snz'}. Access control — recipient'ы только.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.tasks import create_task
from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)


async def handle_dis_callback(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    cq_id: str,
    fb_ad_id: str,
    token: str,
    username: str,
) -> None:
    """dis: создаёт `task_queue` запись на disable (или возвращает «уже в очереди»)."""
    idem_key = f"manual:disable:{fb_ad_id}:{token or 'no-token'}"
    requested_by = f"tg:{username}"
    try:
        task_id = await create_task(
            engine,
            task_type="disable",
            idempotency_key=idem_key,
            payload={
                "fb_ad_id": fb_ad_id,
                "open_state_token": token or None,
            },
            requested_by=requested_by,
        )
        ack = "Задача на отключение принята" if task_id else "Уже в очереди"
    except Exception:
        logger.exception("create_task disable failed")
        ack = "Ошибка"
    try:
        await client.answer_callback_query(cq_id, text=ack)
    except Exception:
        pass


async def handle_enable_reco_callback(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    cq_id: str,
    fb_ad_id: str,
    username: str,
) -> None:
    """ereco: создаёт `task_queue` запись на enable (рекомендация → ручное подтверждение)."""
    idem_key = f"manual:enable:{fb_ad_id}:tg:{username}"
    requested_by = f"tg:{username}"
    try:
        task_id = await create_task(
            engine,
            task_type="enable",
            idempotency_key=idem_key,
            payload={"fb_ad_id": fb_ad_id},
            requested_by=requested_by,
        )
        ack = "Задача на включение принята" if task_id else "Уже в очереди"
    except Exception:
        logger.exception("create_task enable (ereco) failed")
        ack = "Ошибка"
    try:
        await client.answer_callback_query(cq_id, text=ack)
    except Exception:
        pass


async def handle_snz_callback(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    cq_id: str,
    fb_ad_id: str,
) -> None:
    """snz: ставит `ad_alert_state.snoozed_until` = now+2h."""
    snooze_until = datetime.now(timezone.utc) + timedelta(hours=2)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa_text(
                    """
                    UPDATE ad_alert_state s
                    SET snoozed_until = :until, updated_at = NOW()
                    FROM fb_ads a
                    WHERE s.ad_id = a.id AND a.fb_ad_id = :fbid
                    """
                ),
                {"until": snooze_until, "fbid": fb_ad_id},
            )
        ack = "Снуз на 2 часа"
    except Exception:
        logger.exception("snooze failed")
        ack = "Ошибка"
    try:
        await client.answer_callback_query(cq_id, text=ack)
    except Exception:
        pass


__all__ = [
    "handle_dis_callback",
    "handle_enable_reco_callback",
    "handle_snz_callback",
]
