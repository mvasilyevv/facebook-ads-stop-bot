# -*- coding: utf-8 -*-
"""AI-ассистент в Telegram: /ai <вопрос> и свободный текст в личке владельца.

Вход в ChatSession(allow_tools=True): READ_ONLY tools исполняются сразу,
DRAFT_REQUIRED (request_*) создают черновик в task_queue — под ответом
ассистента приходит превью с кнопками ✅/❌ (готовый путь dr_ok/dr_cancel,
см. draft_confirm.py). Owner-ACL — на уровне router.py.

История диалога — Redis-list `ai:chat:hist:{chat_id}` (JSON {role, content}),
обрезается до ai_chat_history_max_messages, TTL ai_chat_history_ttl_seconds.
Без Redis чат работает stateless (каждый вопрос — с чистого листа).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.ai_assistant.chat import (
    ChatMessage,
    ChatRateLimitedError,
    ChatSession,
)
from core.ai_assistant.client import AIUnavailableError
from core.ai_assistant.tools import GLOBAL_REGISTRY
from core.ai_assistant.tools.base import RiskLevel
from core.config import get_settings
from core.telegram import format as fmt
from core.telegram.client import TelegramAPIError, TelegramBotClient
from core.telegram.handlers._send import send_text
from core.telegram.handlers.draft_confirm import draft_inline_keyboard

logger = logging.getLogger(__name__)

_HISTORY_KEY_PREFIX = "ai:chat:hist:"
_BUSY_KEY_PREFIX = "ai:chat:busy:"
# Ответ с tool-циклом может занять пару минут (до 5 раундов × таймаут гейтвея).
_BUSY_TTL_SECONDS = 180

# task_id из текста результата draft-инструмента: "DRAFT создан: task_id=123 (...)"
_TASK_ID_RE = re.compile(r"task_id=(\d+)")

_RESET_WORDS = frozenset({"reset", "сброс"})


def _draft_tool_names() -> frozenset[str]:
    """Имена DRAFT_REQUIRED инструментов из реестра (без хардкода списка)."""
    return frozenset(h.name for h in GLOBAL_REGISTRY.list_by_risk(RiskLevel.DRAFT_REQUIRED))


def _history_key(chat_id: int) -> str:
    return f"{_HISTORY_KEY_PREFIX}{chat_id}"


async def _load_history(redis_client: Any | None, chat_id: int) -> list[ChatMessage]:
    """Прочитать историю диалога из Redis. Любая ошибка → пустая история."""
    if redis_client is None:
        return []
    try:
        raw_items = await redis_client.lrange(_history_key(chat_id), 0, -1)
    except Exception:  # noqa: BLE001 — Redis лёг, чат продолжает stateless
        logger.warning("ai_chat: не смог прочитать историю из Redis", exc_info=True)
        return []
    history: list[ChatMessage] = []
    for raw in raw_items:
        try:
            item = json.loads(raw)
            role = str(item.get("role", ""))
            content = str(item.get("content", ""))
            if role in ("user", "assistant") and content:
                history.append(ChatMessage(role=role, content=content))
        except (ValueError, TypeError, AttributeError):
            continue  # битую запись пропускаем
    return history


async def _append_history(
    redis_client: Any | None,
    chat_id: int,
    *entries: ChatMessage,
) -> None:
    """Дописать обмен в историю + LTRIM до лимита + TTL. Best-effort."""
    if redis_client is None:
        return
    settings = get_settings()
    key = _history_key(chat_id)
    try:
        for e in entries:
            await redis_client.rpush(key, json.dumps({"role": e.role, "content": e.content}))
        await redis_client.ltrim(key, -settings.ai_chat_history_max_messages, -1)
        await redis_client.expire(key, settings.ai_chat_history_ttl_seconds)
    except Exception:  # noqa: BLE001
        logger.warning("ai_chat: не смог сохранить историю в Redis", exc_info=True)


async def _send_answer(
    client: TelegramBotClient,
    *,
    chat_id: int,
    thread_id: int | None,
    text: str,
) -> None:
    """Ответ ассистента: HTML, при невалидной разметке от модели — plain-фолбэк."""
    try:
        await client.send_message(
            chat_id=str(chat_id),
            text=text,
            message_thread_id=thread_id,
            parse_mode="HTML",
        )
    except TelegramAPIError:
        # Модель сгенерировала кривой HTML — Telegram 400. Шлём как есть без разметки.
        try:
            await client.send_message(
                chat_id=str(chat_id),
                text=text,
                message_thread_id=thread_id,
                parse_mode=None,
            )
        except Exception:  # noqa: BLE001
            logger.exception("ai_chat: не смог отправить ответ даже без разметки")
    except Exception:  # noqa: BLE001
        logger.exception("ai_chat: сетевая ошибка отправки ответа")


async def _send_draft_previews(
    client: TelegramBotClient,
    *,
    chat_id: int,
    thread_id: int | None,
    tool_calls: list[Any],
) -> None:
    """Для каждого успешного request_*-вызова — превью черновика с кнопками ✅/❌."""
    draft_names = _draft_tool_names()
    for trace in tool_calls:
        if trace.name not in draft_names or trace.error is not None:
            continue
        m = _TASK_ID_RE.search(trace.result or "")
        if not m:
            logger.warning(
                "ai_chat: draft-инструмент %s не вернул task_id в результате: %r",
                trace.name,
                (trace.result or "")[:120],
            )
            continue
        task_id = int(m.group(1))
        try:
            await client.send_message(
                chat_id=str(chat_id),
                text=(
                    f"📝 {fmt.b(f'Черновик #{task_id}')} · {fmt.code(trace.name)}\n"
                    f"{fmt.esc((trace.result or '')[:500])}\n\n"
                    "Подтверди ✅ / ❌."
                ),
                message_thread_id=thread_id,
                reply_markup=draft_inline_keyboard(task_id),
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            logger.exception("ai_chat: не смог отправить превью черновика #%s", task_id)


async def handle_ai_chat(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
    username: str | None,
    args_text: str,
    redis_client: Any | None = None,
    meta_api_client: Any | None = None,
) -> None:
    """Обработать вопрос владельца к AI-ассистенту (/ai или свободный текст в DM)."""
    settings = get_settings()
    if not settings.ai_tg_chat_enabled:
        await send_text(
            client,
            chat_id=chat_id,
            text="AI-чат выключен настройкой AI_TG_CHAT_ENABLED.",
            message_thread_id=thread_id,
        )
        return

    question = args_text.strip()

    if question.lower() in _RESET_WORDS:
        if redis_client is not None:
            try:
                await redis_client.delete(_history_key(chat_id))
            except Exception:  # noqa: BLE001
                logger.warning("ai_chat: не смог удалить историю", exc_info=True)
        await send_text(
            client,
            chat_id=chat_id,
            text="🧹 Контекст диалога сброшен.",
            message_thread_id=thread_id,
        )
        return

    if not question:
        await send_text(
            client,
            chat_id=chat_id,
            text=(
                f"🤖 Спроси ассистента: {fmt.code('/ai что с кабинетом?')}\n"
                f"В личке можно писать без команды. {fmt.code('/ai reset')} — сброс контекста."
            ),
            message_thread_id=thread_id,
        )
        return

    # Busy-guard: один вопрос за раз на чат (tool-цикл может думать минуту+).
    busy_key = f"{_BUSY_KEY_PREFIX}{chat_id}"
    if redis_client is not None:
        try:
            acquired = await redis_client.set(busy_key, "1", nx=True, ex=_BUSY_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            acquired = True  # Redis лёг — работаем без guard'а
        if not acquired:
            await send_text(
                client,
                chat_id=chat_id,
                text="⏳ Ещё думаю над прошлым вопросом — дождись ответа.",
                message_thread_id=thread_id,
            )
            return

    try:
        # Индикатор «печатает…» — чисто UX, ошибки не важны.
        try:
            await client.send_chat_action(chat_id=str(chat_id), action="typing")
        except Exception:  # noqa: BLE001
            pass

        history = await _load_history(redis_client, chat_id)
        history.append(ChatMessage(role="user", content=question))

        session = ChatSession(
            allow_tools=True,
            engine=engine,
            redis_client=redis_client,
            meta_api_client=meta_api_client,
            skills=("chat_operator",),
        )
        try:
            resp = await session.ask(
                history,
                client_key=f"tg:{chat_id}",
                requested_by=f"tg:{username or chat_id}",
                created_by_chat_id=chat_id,
            )
        except ChatRateLimitedError:
            await send_text(
                client,
                chat_id=chat_id,
                text="🚦 Лимит запросов к ассистенту исчерпан — попробуй через час.",
                message_thread_id=thread_id,
            )
            return
        except AIUnavailableError as exc:
            await send_text(
                client,
                chat_id=chat_id,
                text=f"😴 Ассистент недоступен: {fmt.esc(str(exc))}",
                message_thread_id=thread_id,
            )
            return

        await _send_answer(client, chat_id=chat_id, thread_id=thread_id, text=resp.answer)
        await _send_draft_previews(
            client, chat_id=chat_id, thread_id=thread_id, tool_calls=resp.tool_calls
        )
        await _append_history(
            redis_client,
            chat_id,
            ChatMessage(role="user", content=question),
            ChatMessage(role="assistant", content=resp.answer),
        )
    except Exception:  # noqa: BLE001 — не отдаём исключение поллеру (иначе 3 ретрая вопроса)
        logger.exception("ai_chat: необработанная ошибка (chat_id=%s)", chat_id)
        await send_text(
            client,
            chat_id=chat_id,
            text="💥 Внутренняя ошибка ассистента — детали в логе поллера.",
            message_thread_id=thread_id,
        )
    finally:
        if redis_client is not None:
            try:
                await redis_client.delete(busy_key)
            except Exception:  # noqa: BLE001
                pass


# Живые фоновые таски чата: держим ссылки от GC, чистим по done.
_chat_tasks: set[asyncio.Task] = set()


def spawn_ai_chat(**kwargs: Any) -> None:
    """Запустить handle_ai_chat фоновым таском (ревью H-1).

    Поллер обрабатывает updates строго последовательно: inline-await AI-чата
    (tool-цикл до минут) ставил бы ручные money-кнопки (dis:/ereco:/dr_ok) в
    очередь ЗА ответом ассистента. Фоновый таск возвращает управление сразу;
    параллельные вопросы одного чата отсекает busy-guard внутри handle_ai_chat,
    ошибки хендлер глотает сам (ретрай вопроса поллером не нужен).
    """
    task = asyncio.create_task(handle_ai_chat(**kwargs))
    _chat_tasks.add(task)
    task.add_done_callback(_chat_tasks.discard)


__all__ = ["handle_ai_chat", "spawn_ai_chat"]
