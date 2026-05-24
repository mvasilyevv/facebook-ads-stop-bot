# -*- coding: utf-8 -*-
"""Drain-loop: вычитывает алерты из Redis-очереди и доставляет в Telegram.

Запускается как background-task в apps/telegram_poller/main.py.
При недоставке — requeue с экспоненциальным backoff (до 600 секунд).
После MAX_ATTEMPTS попыток — алерт дропается с error-логом.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from core.alerts.queue import MAX_ATTEMPTS, AlertQueue
from core.metrics import record_alert_sent

logger = logging.getLogger(__name__)

# Базовая задержка при первом retry (секунды)
_BASE_RETRY_DELAY_SECONDS = 60


def _calc_delay(attempt: int) -> int:
    """Экспоненциальный backoff: 60 * 2^attempt, не более 600 секунд."""
    return min(_BASE_RETRY_DELAY_SECONDS * (2 ** max(attempt - 1, 0)), 600)


async def run_drain_loop(queue: AlertQueue, client: object) -> None:
    """Основной цикл вычитки и доставки алёртов.

    Args:
        queue: AlertQueue — источник сообщений.
        client: TelegramBotClient — для отправки.
    """
    logger.info("AlertQueue drain-loop запущен")
    try:
        while True:
            payload = await queue.dequeue_blocking(timeout=5)
            if payload is None:
                # Таймаут BRPOP — продолжаем цикл
                continue

            chat_id = payload.get("chat_id", "")
            text = payload.get("text", "")
            reply_markup = payload.get("reply_markup")
            message_thread_id = payload.get("message_thread_id")
            attempt = payload.get("attempt", 0)

            if not chat_id or not text:
                logger.warning(
                    "AlertQueue drain-loop: пропуск payload без chat_id/text (attempt=%d)",
                    attempt,
                )
                continue

            try:
                # parse_mode передаётся как часть payload,
                # TelegramBotClient.send_message всегда использует HTML —
                # дополнительных параметров не нужно
                kwargs: dict = {}
                if reply_markup:
                    kwargs["reply_markup"] = reply_markup
                if message_thread_id is not None:
                    kwargs["message_thread_id"] = message_thread_id
                await client.send_message(chat_id=chat_id, text=text, **kwargs)  # type: ignore[attr-defined]
                logger.info(
                    "AlertQueue drain-loop: алерт доставлен (chat_id=%s, attempt=%d)",
                    chat_id,
                    attempt,
                )
                # Измеряем latency от создания алёрта до успешной доставки
                created_at_raw = payload.get("created_at")
                if created_at_raw:
                    try:
                        created_dt = datetime.fromisoformat(created_at_raw)
                        if created_dt.tzinfo is None:
                            created_dt = created_dt.replace(tzinfo=UTC)
                        elapsed_ms = (datetime.now(UTC) - created_dt).total_seconds() * 1000
                        record_alert_sent(elapsed_ms)
                    except Exception:
                        pass  # Не прерываем доставку из-за ошибки метрик
            except asyncio.CancelledError:
                # Graceful shutdown — возвращаем алерт в очередь без retry
                await queue.enqueue(payload)
                raise
            except Exception as exc:
                if attempt >= MAX_ATTEMPTS:
                    logger.error(
                        "AlertQueue drain-loop: алерт дропается после %d попыток (chat_id=%s): %s",
                        attempt,
                        chat_id,
                        exc,
                    )
                    continue

                delay = _calc_delay(attempt)
                logger.warning(
                    "AlertQueue drain-loop: ошибка доставки (attempt=%d, retry через %dс): %s",
                    attempt,
                    delay,
                    exc,
                )
                await queue.requeue_with_delay(payload, delay_seconds=delay)

    except asyncio.CancelledError:
        logger.info("AlertQueue drain-loop остановлен (CancelledError)")
        raise
    except Exception as exc:
        logger.exception("AlertQueue drain-loop: необработанная ошибка: %s", exc)
        raise
