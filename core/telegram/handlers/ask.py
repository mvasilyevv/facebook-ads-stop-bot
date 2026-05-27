# -*- coding: utf-8 -*-
"""/ask — AI-ассистент + draft callbacks (dr_ok / dr_cancel).

Создаёт `ChatSession` с пробрасываемыми зависимостями (engine, meta_api_client),
вызывает её в asyncio.Task — main loop poller'а остаётся отзывчивым. По готовности
шлёт финальный ответ + отдельные сообщения-preview под каждый draft с inline-кнопками.

meta_api_client опционален: если None или browser-agent оффлайн, meta-tools
вернут читаемую ошибку через ToolError (см. core/meta_api/errors.py).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.ai_assistant.chat import (
    ChatMessage,
    ChatRateLimitedError,
    ChatSession,
)
from core.ai_assistant.client import AIUnavailableError
from core.meta_api.queue import approve_draft_task, cancel_task
from core.telegram.client import TelegramBotClient
from core.telegram.handlers._send import send_text

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    from core.meta_api.client import MetaApiClient

logger = logging.getLogger(__name__)

DRAFT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "request_budget_change",
        "request_clone_campaign",
        "request_bulk_pause",
        "request_create_campaign",
    }
)
_DRAFT_TASK_ID_RE = re.compile(r"task_id=(\d+)")


def draft_inline_keyboard(task_id: int) -> dict:
    """inline_keyboard для AI draft preview: подтвердить / отклонить."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Подтвердить", "callback_data": f"dr_ok:{task_id}"},
                {"text": "❌ Отклонить", "callback_data": f"dr_cancel:{task_id}"},
            ]
        ]
    }


def extract_draft_task_ids(traces: list[Any]) -> list[tuple[int, str, str]]:
    """Из ChatResponse.tool_calls собрать (task_id, tool_name, result) для draft-tools.

    Парсит task_id регулярно из текста result; элементы без task_id пропускаются.
    """
    out: list[tuple[int, str, str]] = []
    for tr in traces or []:
        tool_name = getattr(tr, "name", "")
        if tool_name not in DRAFT_TOOL_NAMES:
            continue
        if getattr(tr, "error", None):
            continue
        result_text = getattr(tr, "result", "") or ""
        match = _DRAFT_TASK_ID_RE.search(result_text)
        if not match:
            continue
        try:
            task_id = int(match.group(1))
        except (TypeError, ValueError):
            continue
        out.append((task_id, tool_name, result_text))
    return out


async def _handle_ask_background(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    thread_id: int | None,
    question: str,
    user_id: int,
    username: str | None,
    meta_api_client: MetaApiClient | None = None,
) -> None:
    """ChatSession.ask → финальный ответ + draft previews."""
    client_key = f"tg:{user_id}"
    requested_by = f"tg:{username or user_id}"
    session = ChatSession(engine=engine, meta_api_client=meta_api_client)
    try:
        response = await session.ask(
            [ChatMessage(role="user", content=question)],
            client_key=client_key,
            requested_by=requested_by,
        )
    except ChatRateLimitedError as exc:
        await send_text(client, chat_id=chat_id, text=f"⏱ {exc}", message_thread_id=thread_id)
        return
    except AIUnavailableError as exc:
        await send_text(
            client, chat_id=chat_id, text=f"AI недоступен: {exc}", message_thread_id=thread_id
        )
        return
    except Exception:
        logger.exception("ChatSession.ask упал")
        await send_text(
            client,
            chat_id=chat_id,
            text="AI: внутренняя ошибка. Подробности в логах.",
            message_thread_id=thread_id,
        )
        return

    answer = response.answer or "(пустой ответ)"
    await send_text(client, chat_id=chat_id, text=answer, message_thread_id=thread_id)

    drafts = extract_draft_task_ids(response.tool_calls)
    for task_id, tool_name, result_text in drafts:
        try:
            await client.send_message(
                chat_id=str(chat_id),
                text=f"📝 Черновик #{task_id} ({tool_name}):\n{result_text}",
                message_thread_id=thread_id,
                reply_markup=draft_inline_keyboard(task_id),
                parse_mode=None,
            )
        except Exception:
            logger.exception("send draft preview failed for task_id=%s", task_id)


async def handle_ask(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
    user_id: int,
    username: str | None,
    args_text: str,
    meta_api_client: MetaApiClient | None = None,
) -> None:
    """TG-команда /ask: ack «Думаю…» и запуск AI в Task."""
    _ = message_id  # клиент не поддерживает reply_to — оставляем для документации
    question = (args_text or "").strip()
    if not question:
        await send_text(
            client,
            chat_id=chat_id,
            text=(
                "Использование: `/ask <вопрос>`\n"
                "Пример: `/ask какие воркеры живы` "
                "или `/ask покажи статистику по DRC_CR2 за last_7d`."
            ),
            message_thread_id=thread_id,
        )
        return

    await send_text(
        client,
        chat_id=chat_id,
        text="🤖 Думаю…",
        message_thread_id=thread_id,
    )

    asyncio.create_task(
        _handle_ask_background(
            engine=engine,
            client=client,
            chat_id=chat_id,
            thread_id=thread_id,
            question=question,
            user_id=user_id,
            username=username,
            meta_api_client=meta_api_client,
        )
    )


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
    """Обработка dr_ok / dr_cancel callback'ов под AI draft preview.

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
            ok = await approve_draft_task(engine, task_id=task_id, approved_by=approver)
            ack = "Подтверждено, попадает в очередь" if ok else "Уже не draft"
            footer = "✅ Подтверждено" if ok else "ℹ️ Уже обработано"
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
                text=f"Черновик #{task_id}: {footer}",
            )
        except Exception:
            logger.debug("edit_message under draft callback failed (некритично)")


__all__ = [
    "DRAFT_TOOL_NAMES",
    "draft_inline_keyboard",
    "extract_draft_task_ids",
    "handle_ask",
    "handle_draft_callback",
]
