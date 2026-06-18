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

from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)


async def _create_toggle_mutation(
    engine: AsyncEngine,
    *,
    mutation_kind: str,
    fb_ad_id: str,
    idempotency_key: str,
    requested_by: str,
) -> int | None:
    """Создать pending meta_api_mutation (pause_ad/activate_ad) для ручной кнопки."""
    from core.meta_api.queue import create_mutation_task
    from core.meta_api.schemas import MetaMutationPayload
    from core.observer.accounts import load_ad_account_id_for_fb_ad

    # Мульти-кабинет: кабинет из каталога (записан observer'ом при скане) —
    # mutation уйдёт из вкладки своего кабинета. None → legacy primary-вкладка.
    ad_account_id = await load_ad_account_id_for_fb_ad(engine, fb_ad_id)
    payload = MetaMutationPayload(
        mutation_kind=mutation_kind,
        target_id=fb_ad_id,
        params={},
        ad_account_id=ad_account_id,
    )
    return await create_mutation_task(
        engine,
        payload=payload,
        requested_by=requested_by,
        status="pending",
        idempotency_key=idempotency_key,
    )


async def handle_dis_callback(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    cq_id: str,
    fb_ad_id: str,
    token: str,
    username: str,
) -> None:
    """dis: создаёт задачу на отключение через Marketing API (pause_ad)."""
    requested_by = f"tg:{username}"
    try:
        task_id = await _create_toggle_mutation(
            engine,
            mutation_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            idempotency_key=f"manual:pause_ad:{fb_ad_id}:{token or 'no-token'}",
            requested_by=requested_by,
        )
        # L2: помечаем инцидент claimed (человек взял управление) — чтобы observer
        # не плодил параллельную auto-pause задачу. Best-effort, не ломает ack.
        try:
            from core.observer.writers import mark_alert_state_claimed

            await mark_alert_state_claimed(engine, fb_ad_id=fb_ad_id)
        except Exception:
            logger.warning("dis: не удалось пометить claimed для %s", fb_ad_id, exc_info=True)
        ack = "Задача на отключение принята" if task_id else "Уже в очереди"
    except Exception:
        logger.exception("create disable task failed")
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
    """ereco: создаёт задачу на включение через Marketing API (activate_ad)."""
    requested_by = f"tg:{username}"
    try:
        task_id = await _create_toggle_mutation(
            engine,
            mutation_kind="activate_ad",
            fb_ad_id=fb_ad_id,
            idempotency_key=f"manual:activate_ad:{fb_ad_id}:tg:{username}",
            requested_by=requested_by,
        )
        ack = "Задача на включение принята" if task_id else "Уже в очереди"
    except Exception:
        logger.exception("create enable task (ereco) failed")
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
