# -*- coding: utf-8 -*-
"""WebSocket endpoint /ws/dashboard.

Форвардит события Redis pub/sub всем подключённым браузерам.
Поддерживает heartbeat (ping каждые 30 секунд), чтобы прокси/браузер
не рвал idle-соединение.
"""

from __future__ import annotations

import asyncio
import logging

from core.pubsub import ALL_DASHBOARD_CHANNELS, RedisPubSub
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Интервал ping-фрейма (сек) — браузер знает, что соединение живое
_PING_INTERVAL = 30


@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket) -> None:
    """WebSocket-канал для realtime-обновлений дашборда.

    Принимает любое соединение (аутентификация MVP — открыта).
    На каждое событие Redis отправляет JSON-сообщение клиенту.
    Каждые 30 сек отправляет {"type": "ping"}, чтобы держать соединение.
    """
    await websocket.accept()
    settings = get_settings()
    pubsub = RedisPubSub(settings.redis_url)

    async def _send_pings() -> None:
        """Фоновая задача: ping каждые _PING_INTERVAL секунд."""
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            try:
                await websocket.send_json({"type": "ping"})
            except Exception:
                # WebSocket уже закрыт — прекращаем
                return

    ping_task: asyncio.Task | None = None
    try:
        ping_task = asyncio.create_task(_send_pings(), name="ws-ping")
        logger.debug("WS /dashboard: клиент подключился")

        async for event in pubsub.subscribe(ALL_DASHBOARD_CHANNELS):
            try:
                await websocket.send_json(event)
            except (WebSocketDisconnect, RuntimeError):
                # Клиент отключился
                break
    except WebSocketDisconnect:
        logger.debug("WS /dashboard: клиент отключился")
    except Exception:
        logger.warning("WS /dashboard: ошибка в цикле событий", exc_info=True)
    finally:
        if ping_task is not None and not ping_task.done():
            ping_task.cancel()
            try:
                await ping_task
            except (asyncio.CancelledError, Exception):
                pass
        await pubsub.close()
        logger.debug("WS /dashboard: соединение закрыто")
