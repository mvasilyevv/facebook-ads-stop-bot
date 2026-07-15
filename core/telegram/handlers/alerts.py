# -*- coding: utf-8 -*-
"""Inline-кнопки под алертами: dis (disable) и ereco (enable recommendation).

callback_data: '<action>:<fb_ad_id>[:<token>]' (см. renderer.render_inline_keyboard).
action ∈ {'dis', 'ereco'}. Access control — recipient'ы только.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.enable_reco.confirmation import (
    RecommendationAlreadyPromotedError,
    RecommendationNotFoundError,
    RecommendationUnsafeStateError,
    promote_enable_recommendation,
)
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


async def _current_open_token_matches(engine: AsyncEngine, *, fb_ad_id: str, token: str) -> bool:
    """Сверяет token из callback-кнопки с open_state_token текущего инцидента.

    H-4 (replay-защита): token в кнопке `dis:<fb>:<token>` привязан к open_state_token
    инцидента, в рамках которого алерт был отправлен. Если объявление успело восстановиться
    (инцидент закрыт → open_state_token=NULL) или ушло в НОВЫЙ инцидент (новый token), клик
    по СТАРОЙ кнопке из истории чата должен отклоняться — иначе восстановленное объявление
    паузится по протухшей кнопке.

    Эскалация warning_sent→stop_sent сохраняет open_state_token (см. state_machine.decide),
    поэтому старая WARNING-кнопка того же инцидента продолжает совпадать — инвариант HIGH #10.

    Возвращает True только при точном совпадении. Отсутствие строки state, NULL-token
    (инцидент закрыт) или пустой token в кнопке → False (отказ).
    """
    from core.observer.queries import load_alert_state_by_fb_ad_id

    if not token:
        return False
    snapshot_map = await load_alert_state_by_fb_ad_id(engine, fb_ad_ids=[fb_ad_id])
    snapshot = snapshot_map.get(fb_ad_id)
    if snapshot is None or snapshot.open_state_token is None:
        return False
    return str(snapshot.open_state_token) == token


async def handle_dis_callback(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    cq_id: str,
    fb_ad_id: str,
    token: str,
    username: str,
) -> None:
    """dis: создаёт задачу на отключение через Marketing API (pause_ad).

    Перед созданием задачи сверяет token кнопки с open_state_token текущего инцидента
    (H-4): протухшая кнопка из прошлого инцидента отклоняется, задача не создаётся.
    """
    requested_by = f"tg:{username}"

    # H-4: replay-защита. Кнопка из закрытого/чужого инцидента — отказ до любых side-эффектов.
    try:
        token_ok = await _current_open_token_matches(engine, fb_ad_id=fb_ad_id, token=token)
    except Exception:
        logger.exception("dis: не удалось сверить open_state_token для %s", fb_ad_id)
        token_ok = False
    if not token_ok:
        logger.warning(
            "dis: отклонён устаревший алерт fb_ad_id=%s token=%s (состояние изменилось)",
            fb_ad_id,
            token or "<empty>",
        )
        try:
            await client.answer_callback_query(
                cq_id, text="Алерт устарел — состояние объявления изменилось"
            )
        except Exception:
            pass
        return

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
    recommendation_id: str,
    username: str,
    chat_id: int | None = None,
    redis_client: Any | None = None,
) -> None:
    """ereco:<recommendation_uuid>: revalidate и создать activate_ad атомарно."""
    _ = redis_client  # legacy dependency; grace ставит worker после успеха activation.
    requested_by = f"tg:{username}"
    try:
        promotion = await promote_enable_recommendation(
            engine,
            recommendation_id=recommendation_id,
            requested_by=requested_by,
            created_by_chat_id=chat_id,
        )
        ack = f"Задача на включение принята (#{promotion.task_id})"
    except (RecommendationNotFoundError, RecommendationAlreadyPromotedError):
        ack = "Рекомендация устарела"
    except RecommendationUnsafeStateError as exc:
        logger.warning("ereco отклонена: %s", exc)
        ack = str(exc)
    except Exception:
        logger.exception("create enable task (ereco) failed")
        ack = "Ошибка"
    await _answer(client, cq_id, ack)


async def _answer(client: TelegramBotClient, cq_id: str, text: str) -> None:
    """Best-effort ответ на callback query (не роняет обработчик)."""
    try:
        await client.answer_callback_query(cq_id, text=text)
    except Exception:
        pass


__all__ = [
    "handle_dis_callback",
    "handle_enable_reco_callback",
]
