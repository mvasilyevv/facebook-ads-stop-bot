# -*- coding: utf-8 -*-
"""Redis Pub/Sub — шина событий между воркерами и API.

Channels (константы ниже):
  fb_agent:scan:finished   — observer завершил цикл скана
  fb_agent:alert:created   — создан новый AlertEvent
  fb_agent:task:changed    — изменился статус DisableTask/EnableTask
  fb_agent:health:updated  — обновился health-статус воркера
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import AsyncIterator

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Публичные константы каналов
# ---------------------------------------------------------------------------

CHANNEL_SCAN_FINISHED = "fb_agent:scan:finished"
CHANNEL_ALERT_CREATED = "fb_agent:alert:created"
CHANNEL_TASK_CHANGED = "fb_agent:task:changed"
CHANNEL_HEALTH_UPDATED = "fb_agent:health:updated"

ALL_DASHBOARD_CHANNELS = [
    CHANNEL_SCAN_FINISHED,
    CHANNEL_ALERT_CREATED,
    CHANNEL_TASK_CHANGED,
    CHANNEL_HEALTH_UPDATED,
]


class RedisPubSub:
    """Тонкая обёртка над redis.asyncio pub/sub для шины событий."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        # Один клиент для publish; subscribe создаёт отдельный для каждого вызова
        self._publisher: aioredis.Redis | None = None

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def _get_publisher(self) -> aioredis.Redis:
        """Ленивый синглтон-клиент для публикации."""
        if self._publisher is None:
            self._publisher = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._publisher

    async def publish(self, channel: str, event: dict) -> None:
        """Публикует событие в Redis-канал.

        Добавляет поле ``timestamp`` (ISO-8601 UTC), если его нет в event.
        """
        if "timestamp" not in event:
            event = {**event, "timestamp": datetime.now(UTC).isoformat()}
        try:
            client = await self._get_publisher()
            await client.publish(channel, json.dumps(event, ensure_ascii=False))
        except Exception:
            # Не падаем если Redis недоступен — publish не критичен
            logger.warning(
                "RedisPubSub: не удалось опубликовать событие в %s", channel, exc_info=True
            )

    # ------------------------------------------------------------------
    # Subscribe
    # ------------------------------------------------------------------

    async def subscribe(self, channels: list[str]) -> AsyncIterator[dict]:
        """Подписывается на каналы и отдаёт события как async-генератор.

        Создаёт отдельное подключение (pubsub-режим несовместим с publish).
        Генератор завершается только при вызове ``close()`` или возникновении
        исключения.
        """
        # Отдельное подключение для подписки
        sub_client: aioredis.Redis = aioredis.from_url(
            self._redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        pubsub = sub_client.pubsub()
        try:
            await pubsub.subscribe(*channels)
            async for raw in pubsub.listen():
                if raw is None:
                    continue
                if raw.get("type") != "message":
                    continue
                data = raw.get("data", "")
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    logger.debug("RedisPubSub: нечитаемое сообщение: %s", data)
                    continue
                yield event
        finally:
            try:
                await pubsub.unsubscribe(*channels)
                await pubsub.close()
            except Exception:
                pass
            try:
                await sub_client.aclose()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Закрывает publish-соединение."""
        if self._publisher is not None:
            try:
                await self._publisher.aclose()
            except Exception:
                pass
            self._publisher = None
