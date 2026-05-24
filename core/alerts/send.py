# -*- coding: utf-8 -*-
"""Обёртка для отправки критичных Telegram-алёртов через Redis-очередь.

Функция send_telegram_via_queue() является транспортным слоем:
- при alerts_queue_enabled=True → помещает сообщение в Redis-очередь;
- при alerts_queue_enabled=False → отправляет напрямую через TelegramBotClient;
- при недоступности Redis → пытается отправить напрямую (fallback).

НЕ использовать для ответов на команды бота — только для критичных алёртов.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Синглтон очереди — инициализируется при первом вызове
_queue: object | None = None


def _get_queue() -> object:
    """Лениво инициализирует синглтон AlertQueue."""
    global _queue
    if _queue is None:
        from core.alerts.queue import AlertQueue
        from core.config import get_settings

        cfg = get_settings()
        _queue = AlertQueue(redis_url=cfg.redis_url)
    return _queue


async def send_telegram_via_queue(
    chat_id: str | int,
    text: str,
    *,
    fallback_client: "object | None" = None,
    reply_markup: dict | None = None,
    message_thread_id: int | None = None,
) -> None:
    """Отправляет критичный алерт через Redis-очередь (или напрямую при fallback).

    Args:
        chat_id: Telegram chat ID получателя.
        text: Текст сообщения (HTML-форматирование).
        fallback_client: TelegramBotClient для прямой отправки, если Redis недоступен.
            Если передан — используется вместо создания нового клиента по bot_token.
        reply_markup: Inline-клавиатура (опционально).
        message_thread_id: ID топика в супергруппе (опционально).
    """
    from core.config import get_settings

    cfg = get_settings()

    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id

    if not cfg.alerts_queue_enabled:
        # Очередь отключена — отправляем напрямую
        await _send_direct(cfg, payload, fallback_client=fallback_client)
        return

    queue = _get_queue()
    # Проверяем доступность Redis перед постановкой в очередь
    healthy = await queue.health()  # type: ignore[attr-defined]
    if not healthy:
        logger.warning(
            "AlertQueue: Redis недоступен — fallback прямая отправка (chat_id=%s)", chat_id
        )
        await _send_direct(cfg, payload, fallback_client=fallback_client)
        return

    await queue.enqueue(payload)  # type: ignore[attr-defined]


async def _send_direct(
    cfg: object,
    payload: dict,
    *,
    fallback_client: "object | None" = None,
) -> None:
    """Прямая отправка через TelegramBotClient (fallback или queue disabled).

    Если передан fallback_client — использует его send_message без создания нового клиента.
    """
    kwargs: dict = {}
    if payload.get("reply_markup"):
        kwargs["reply_markup"] = payload["reply_markup"]
    if payload.get("message_thread_id") is not None:
        kwargs["message_thread_id"] = payload["message_thread_id"]

    if fallback_client is not None:
        # Используем переданный клиент — не создаём новый и не вызываем close()
        try:
            await fallback_client.send_message(  # type: ignore[attr-defined]
                chat_id=payload["chat_id"],
                text=payload["text"],
                **kwargs,
            )
        except Exception as exc:
            logger.error(
                "send_telegram_via_queue: прямая отправка не удалась (chat_id=%s): %s",
                payload.get("chat_id", "?"),
                exc,
            )
        return

    from core.telegram.client import TelegramBotClient

    bot_token = getattr(cfg, "telegram_bot_token", "")
    if not bot_token:
        logger.error("send_telegram_via_queue: bot_token не задан, алерт потерян")
        return
    client = TelegramBotClient(bot_token=bot_token)
    try:
        await client.send_message(
            chat_id=payload["chat_id"],
            text=payload["text"],
            **kwargs,
        )
    except Exception as exc:
        logger.error(
            "send_telegram_via_queue: прямая отправка не удалась (chat_id=%s): %s",
            payload.get("chat_id", "?"),
            exc,
        )
    finally:
        await client.close()
