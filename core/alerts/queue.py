# -*- coding: utf-8 -*-
"""Persistent-очередь Telegram-алёртов через Redis.

Принцип работы:
- enqueue() → LPUSH в Redis-list (голова очереди).
- dequeue_blocking() → BRPOP с таймаутом (хвост очереди, FIFO).
- requeue_with_delay() → при ошибке доставки возвращает payload обратно с
  инкрементом attempt; задержка реализована через asyncio.sleep перед повторным
  LPUSH, чтобы не блокировать event loop (спит в фоновом task).
- Graceful degradation: если Redis недоступен при enqueue — fallback или лог.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Таймаут попытки подключения к Redis при проверке здоровья
_HEALTH_TIMEOUT_SECONDS = 2.0

# Максимальное число попыток доставки алерта
MAX_ATTEMPTS = 10


class AlertQueue:
    """Redis-очередь для надёжной доставки Telegram-алёртов."""

    def __init__(
        self,
        redis_url: str,
        queue_name: str = "fb_agent:alerts:pending",
        max_size: int = 10000,
    ) -> None:
        self._redis_url = redis_url
        self._queue_name = queue_name
        self._max_size = max_size
        # Клиент инициализируется лениво при первом вызове
        self._redis: object | None = None

    def _get_redis(self) -> object:
        """Возвращает (или создаёт) async-клиент Redis."""
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=5,
            )
        return self._redis

    async def enqueue(self, payload: dict) -> None:
        """Добавляет payload в голову очереди (LPUSH).

        При недоступности Redis логирует ошибку, но не бросает исключение,
        чтобы не прерывать основной рабочий цикл.
        """
        if "created_at" not in payload:
            payload = {**payload, "created_at": datetime.now(UTC).isoformat()}
        if "attempt" not in payload:
            payload = {**payload, "attempt": 0}

        try:
            r = self._get_redis()
            # Не даём очереди расти бесконечно
            current_size = await asyncio.wait_for(r.llen(self._queue_name), timeout=2.0)  # type: ignore[attr-defined]
            if current_size >= self._max_size:
                logger.error(
                    "AlertQueue: очередь переполнена (%d >= %d), алерт отброшен: chat_id=%s",
                    current_size,
                    self._max_size,
                    payload.get("chat_id", "?"),
                )
                return
            await asyncio.wait_for(
                r.lpush(self._queue_name, json.dumps(payload, ensure_ascii=False)),  # type: ignore[attr-defined]
                timeout=2.0,
            )
            logger.debug(
                "AlertQueue: enqueue OK (chat_id=%s, attempt=%d)",
                payload.get("chat_id", "?"),
                payload.get("attempt", 0),
            )
        except asyncio.TimeoutError:
            logger.error(
                "AlertQueue: таймаут при записи в Redis — алерт не поставлен в очередь "
                "(chat_id=%s)",
                payload.get("chat_id", "?"),
            )
        except Exception as exc:
            logger.error(
                "AlertQueue: ошибка Redis при enqueue (chat_id=%s): %s",
                payload.get("chat_id", "?"),
                exc,
            )

    async def dequeue_blocking(self, timeout: int = 5) -> dict | None:  # noqa: ASYNC109
        """Извлекает следующий payload из хвоста очереди (BRPOP, FIFO).

        Возвращает None при таймауте или ошибке Redis.
        """
        try:
            r = self._get_redis()
            result = await asyncio.wait_for(
                r.brpop(self._queue_name, timeout=timeout),  # type: ignore[attr-defined]
                timeout=timeout + 2.0,
            )
            if result is None:
                return None
            _key, raw = result
            return json.loads(raw)
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("AlertQueue: ошибка Redis при dequeue: %s", exc)
            # Небольшая пауза, чтобы не уйти в тугой цикл при битом соединении
            await asyncio.sleep(1.0)
            return None

    async def requeue_with_delay(self, payload: dict, delay_seconds: int) -> None:
        """Повторно ставит payload в очередь с задержкой и инкрементом attempt.

        Задержка реализована через background-task (asyncio.sleep),
        чтобы не блокировать drain-loop.
        """
        new_payload = {**payload, "attempt": payload.get("attempt", 0) + 1}

        async def _delayed_enqueue() -> None:
            await asyncio.sleep(delay_seconds)
            await self.enqueue(new_payload)
            logger.info(
                "AlertQueue: requeue после %dс (attempt=%d, chat_id=%s)",
                delay_seconds,
                new_payload["attempt"],
                new_payload.get("chat_id", "?"),
            )

        asyncio.create_task(_delayed_enqueue())

    async def size(self) -> int:
        """Возвращает текущий размер очереди (LLEN)."""
        try:
            r = self._get_redis()
            return await asyncio.wait_for(r.llen(self._queue_name), timeout=2.0)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("AlertQueue: не удалось получить размер очереди: %s", exc)
            return -1

    async def health(self) -> bool:
        """Проверяет доступность Redis через PING. True = доступен."""
        try:
            r = self._get_redis()
            await asyncio.wait_for(r.ping(), timeout=_HEALTH_TIMEOUT_SECONDS)  # type: ignore[attr-defined]
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Закрывает соединение с Redis."""
        if self._redis is not None:
            try:
                await self._redis.aclose()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._redis = None
