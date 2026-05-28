# -*- coding: utf-8 -*-
"""Общий Redis Pub/Sub helper для воркеров.

RedisPubSubListener подписывается на переданные каналы и диспетчеризует входящие
сообщения зарегистрированным handler'ам. Предназначен для привязки воркеров к
управляющим сигналам из API (force-scan, graceful-restart и т.п.).

Использование:
    listener = RedisPubSubListener(redis_client, ["fb_agent:worker:restart:observer"])
    listener.register("fb_agent:worker:restart:observer", my_handler)
    task = asyncio.create_task(listener.run_forever())
    ...
    await listener.stop()
    await task
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Тип handler'а: получает dict-payload, ничего не возвращает.
MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class RedisPubSubListener:
    """Async-листенер Redis Pub/Sub с handler-диспетчером.

    Один экземпляр — одна подписка. Все handlers вызываются последовательно
    (await каждый). Исключения из handlers логируются, loop не прерывается.

    Использует неблокирующий poll (timeout=0) с коротким sleep между итерациями
    для совместимости с fakeredis в тестовом окружении.
    """

    # Пауза между итерациями poll-loop (в секундах).
    POLL_INTERVAL: float = 0.05

    def __init__(self, redis_client: Any, channels: list[str]) -> None:
        """
        Args:
            redis_client: redis.asyncio.Redis-совместимый клиент (в т.ч. fakeredis).
            channels: список каналов для подписки.
        """
        self._redis = redis_client
        self._channels = list(channels)
        # channel → список зарегистрированных handler'ов
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._stop_event = asyncio.Event()

    def register(self, channel: str, handler: MessageHandler) -> None:
        """Зарегистрировать handler для канала. Можно несколько на один канал."""
        self._handlers.setdefault(channel, []).append(handler)

    async def run_forever(self) -> None:
        """Запускает background-loop с subscribe/listen.

        Блокирует выполнение до вызова stop(). При потере соединения — логирует
        исключение и делает pause 1 сек перед переподпиской.
        """
        while not self._stop_event.is_set():
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(*self._channels)
                # Сбрасываем subscribe-confirmation сообщения (по одному на каждый канал).
                # fakeredis не пропускает их через ignore_subscribe_messages=True при
                # timeout=0 — нужно явно прочитать перед обработкой реальных сообщений.
                for _ in self._channels:
                    try:
                        await pubsub.get_message(ignore_subscribe_messages=False, timeout=0)
                    except Exception:
                        pass
                logger.info(
                    "pubsub_listener: подписан на каналы %s",
                    ", ".join(self._channels),
                )
                # Основной loop чтения — неблокирующий poll с asyncio.sleep.
                while not self._stop_event.is_set():
                    try:
                        msg = await pubsub.get_message(
                            ignore_subscribe_messages=True,
                            timeout=0,
                        )
                    except Exception:
                        logger.exception("pubsub_listener: ошибка чтения сообщения")
                        await asyncio.sleep(1.0)
                        break  # переподключиться

                    if msg is not None:
                        channel = msg.get("channel", "")
                        if isinstance(channel, bytes):
                            channel = channel.decode("utf-8", errors="replace")

                        raw_data = msg.get("data", "")
                        if isinstance(raw_data, bytes):
                            raw_data = raw_data.decode("utf-8", errors="replace")

                        await self._dispatch(channel, raw_data)
                    else:
                        # Нет сообщений — yield event loop и проверим снова через POLL_INTERVAL.
                        await asyncio.sleep(self.POLL_INTERVAL)

            except Exception:
                logger.exception("pubsub_listener: ошибка подписки")
                await asyncio.sleep(1.0)
            finally:
                try:
                    await pubsub.unsubscribe(*self._channels)
                except Exception:
                    pass
                try:
                    await pubsub.aclose()
                except Exception:
                    pass

    async def _dispatch(self, channel: str, raw_data: str) -> None:
        """Разобрать payload и вызвать зарегистрированные handler'ы."""
        # Парсим JSON; если не JSON — оборачиваем в {"raw": ...}
        try:
            payload: dict[str, Any] = json.loads(raw_data) if raw_data else {}
            if not isinstance(payload, dict):
                payload = {"raw": payload}
        except (json.JSONDecodeError, ValueError):
            payload = {"raw": raw_data}

        handlers = self._handlers.get(channel, [])
        if not handlers:
            logger.debug("pubsub_listener: нет handler'ов для канала %s", channel)
            return

        for handler in handlers:
            try:
                await handler(payload)
            except Exception:
                logger.exception(
                    "pubsub_listener: handler %s для канала %s упал с ошибкой",
                    getattr(handler, "__name__", repr(handler)),
                    channel,
                )

    async def stop(self) -> None:
        """Graceful stop — выставляет флаг, loop завершается на следующей итерации."""
        self._stop_event.set()
        logger.info("pubsub_listener: получен сигнал остановки")
