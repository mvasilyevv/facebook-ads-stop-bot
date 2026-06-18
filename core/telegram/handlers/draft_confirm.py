# -*- coding: utf-8 -*-
"""Подтверждение draft-задач в Telegram: dr_ok / dr_cancel.

Общий плумбинг подтверждения черновиков `task_queue` (meta_api_mutation).
Используется ручными операторскими командами `/pause` `/resume` (см. bulk.py):
оператор получает превью с кнопками ✅ / ❌, на dr_ok DRAFT → PENDING (исполняет
meta_api_worker), на dr_cancel → CANCELLED. Owner-ACL на dr_ok — в router.py.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.queue import approve_draft_task, cancel_task, is_admin_recipient
from core.telegram import format as fmt
from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)


def draft_inline_keyboard(task_id: int) -> dict:
    """inline_keyboard для draft preview: подтвердить / отклонить."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Подтвердить", "callback_data": f"dr_ok:{task_id}"},
                {"text": "❌ Отклонить", "callback_data": f"dr_cancel:{task_id}"},
            ]
        ]
    }


async def handle_draft_callback(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    cq_id: str,
    action: str,
    task_id_raw: str,
    username: str,
    chat_id: int,
    message_id: int | None,
) -> None:
    """Обработка dr_ok / dr_cancel callback'ов под draft preview.

    action ∈ {'dr_ok', 'dr_cancel'}. На dr_ok DRAFT → PENDING; на dr_cancel → CANCELLED.
    """
    try:
        task_id = int(task_id_raw)
    except (TypeError, ValueError):
        try:
            await client.answer_callback_query(cq_id, text="Некорректный task_id")
        except Exception:
            pass
        return

    approver = f"tg:{username}" if username else f"tg:{chat_id}"
    try:
        if action == "dr_ok":
            # Owner ACL: пробуем сначала approve как owner (по совпадению chat_id).
            # Если в БД не нашлось — отделяем «уже не draft» от «чужой» через SELECT
            # status/created_by_chat_id, а админ-override применяем как fallback.
            ok = await approve_draft_task(
                engine,
                task_id=task_id,
                approved_by=approver,
                approver_chat_id=chat_id,
            )
            if not ok:
                row = await _fetch_task_acl_state(engine, task_id=task_id)
                if row is None:
                    ack = "Черновик не найден"
                    footer = "ℹ️ Уже удалён"
                elif row["status"] != "draft":
                    ack = "Уже не draft"
                    footer = "ℹ️ Уже обработано"
                elif await is_admin_recipient(engine, chat_id=chat_id):
                    # Передаём approver_chat_id — approve_draft_task теперь
                    # верифицирует is_admin внутри (двойная защита, но без повторного
                    # SQL-запроса потому что check выше уже подтвердил роль).
                    ok = await approve_draft_task(
                        engine,
                        task_id=task_id,
                        approved_by=approver,
                        approver_chat_id=chat_id,
                        admin_override=True,
                    )
                    ack = "Подтверждено админом" if ok else "Уже не draft"
                    footer = "✅ Подтверждено (admin)" if ok else "ℹ️ Уже обработано"
                else:
                    logger.warning(
                        "draft approve отказан: task_id=%s, chat_id=%s — чужой draft",
                        task_id,
                        chat_id,
                    )
                    ack = "Этот черновик принадлежит другому пользователю"
                    footer = "🔒 Чужой черновик"
            else:
                ack = "Подтверждено, попадает в очередь"
                footer = "✅ Подтверждено"
        else:  # dr_cancel
            ok = await cancel_task(
                engine,
                task_id=task_id,
                reason=f"cancelled via TG by {approver}",
            )
            ack = "Отменено" if ok else "Уже в финальном статусе"
            footer = "❌ Отменено" if ok else "ℹ️ Уже обработано"
    except Exception:
        logger.exception("draft callback %s task_id=%s упал", action, task_id)
        try:
            await client.answer_callback_query(cq_id, text="Ошибка БД")
        except Exception:
            pass
        return

    try:
        await client.answer_callback_query(cq_id, text=ack)
    except Exception:
        pass

    if message_id:
        try:
            await client.edit_message(
                chat_id=str(chat_id),
                message_id=int(message_id),
                text=f"📝 {fmt.b(f'Черновик #{task_id}')}: {footer}",
            )
        except Exception:
            logger.debug("edit_message under draft callback failed (некритично)")


async def _fetch_task_acl_state(
    engine: AsyncEngine,
    *,
    task_id: int,
) -> dict | None:
    """SELECT (status, created_by_chat_id) у task_queue.id — для разбора отказа approve.

    Возвращает None если запись не найдена.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT status, created_by_chat_id
                    FROM task_queue
                    WHERE id = :id AND task_type = 'meta_api_mutation'
                    """
                ),
                {"id": int(task_id)},
            )
        ).first()
    if row is None:
        return None
    return {"status": str(row[0]), "created_by_chat_id": row[1]}


__all__ = [
    "draft_inline_keyboard",
    "handle_draft_callback",
]
