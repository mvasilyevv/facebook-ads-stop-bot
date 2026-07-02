# -*- coding: utf-8 -*-
"""WebSocket endpoint /ws/dashboard — мост Redis pubsub → фронт.

Каждое WS-соединение создаёт собственный pubsub-подписчик (отдельный Redis-коннект).
Такой паттерн прост, надёжен и не требует shared broadcast manager'а при текущем
масштабе (единственный браузер / небольшая команда).

Контракт сообщений (JSON):
    {"type": "scan_finished"|"alert_created"|"task_changed"|"ping",
     "ts": "<iso-8601-utc>",
     "payload": { ... }}

Интервал heartbeat задаётся через env WS_HEARTBEAT_SECONDS (по умолчанию 30).
Уменьши до 1–2 в тестах через os.environ["WS_HEARTBEAT_SECONDS"] = "1".

Нюанс тестирования:
    В тестах используй app.state.ws_pubsub_redis = <fakeredis_instance> —
    тогда WS-хендлер будет слушать pubsub на этом клиенте вместо создания
    нового соединения по redis_url. Это позволяет тестам publish() и проверять
    что клиент получил сообщение, не поднимая реальный Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from datetime import UTC, datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.pubsub import (
    ALL_DASHBOARD_CHANNELS,
    CHANNEL_ALERT_CREATED,
    CHANNEL_SCAN_FINISHED,
    CHANNEL_TASK_CHANGED,
)

logger = logging.getLogger(__name__)

# Интервал heartbeat-пинга (секунды). Задаётся через env для тестов.
_HEARTBEAT_SECONDS: int = int(os.environ.get("WS_HEARTBEAT_SECONDS", "30"))

# Маппинг Redis-канал → тип события в протоколе фронта.
# ALL_DASHBOARD_CHANNELS включает fb_agent:health:updated — в него публикует
# health_watchdog (apps/health_watchdog/main.py::_publish_health_updated; тип «health_updated»).
_CHANNEL_TO_TYPE: dict[str, str] = {
    CHANNEL_SCAN_FINISHED: "scan_finished",
    CHANNEL_ALERT_CREATED: "alert_created",
    CHANNEL_TASK_CHANGED: "task_changed",
    "fb_agent:health:updated": "health_updated",
}

router = APIRouter(tags=["websocket"])


def _now_iso() -> str:
    """Текущее время UTC в ISO-8601."""
    return datetime.now(UTC).isoformat()


def _make_message(type_: str, payload: dict) -> str:
    """Собирает JSON-сообщение по контракту фронта."""
    return json.dumps({"type": type_, "ts": _now_iso(), "payload": payload}, ensure_ascii=False)


@router.websocket("/ws/dashboard")
async def ws_dashboard(websocket: WebSocket) -> None:  # noqa: C901
    """Real-time push событий сканера, алертов и задач через WebSocket.

    Жизненный цикл соединения:
    1. accept() — подтверждаем соединение.
    2. Получаем Redis-клиент для pubsub:
       - Если app.state.ws_pubsub_redis задан — используем его (тесты с fakeredis).
       - Иначе создаём новый клиент через redis_url из настроек.
    3. Подписываемся на ALL_DASHBOARD_CHANNELS.
    4. Параллельно запускаем два asyncio.Task:
       - _pubsub_loop: читает сообщения из Redis, форвардит клиенту.
       - _heartbeat_loop: шлёт ping каждые _HEARTBEAT_SECONDS секунд.
    5. Ожидаем завершения любого из них (первый отменяет второй).
    6. Cleanup: unsubscribe, закрытие pubsub и sub-клиента (если создавали), отмена задач.
    """
    # M2: auth ДО accept(). BaseHTTPMiddleware (ApiKeyAuthMiddleware) не покрывает
    # WS scope, поэтому канал утекал real-time money-данные (fb_ad_id, STOP-события,
    # rule_codes) без ключа. Токен в query-param (?api_key=), т.к. браузерный WebSocket
    # не умеет слать кастомные заголовки. Тот же X-API-Key, timing-safe сравнение.
    from core.config import get_settings as _get_settings
    from core.config import reveal_secret

    settings = _get_settings()
    if settings.require_api_key:
        expected = reveal_secret(settings.api_key) if settings.api_key else ""
        provided = (
            websocket.query_params.get("api_key") or websocket.query_params.get("token") or ""
        )
        if not expected or not provided or not secrets.compare_digest(provided, expected):
            logger.warning("WS /ws/dashboard: отклонён до accept (нет/неверный api_key)")
            await websocket.close(code=1008)  # policy violation
            return

    await websocket.accept()
    logger.info("WS /ws/dashboard: клиент подключился")

    app_state = websocket.app.state

    # Получаем Redis-клиент для подписки.
    # Тесты могут задать app.state.ws_pubsub_redis = <fakeredis> — тогда используем его
    # напрямую без создания нового соединения. Клиент в этом случае НЕ закрываем —
    # владелец (тест/фикстура) управляет его жизненным циклом.
    injected_client: aioredis.Redis | None = getattr(app_state, "ws_pubsub_redis", None)
    own_sub_client = False  # флаг: мы создали клиент, нам и закрывать

    if injected_client is not None:
        sub_client: aioredis.Redis = injected_client
    else:
        # В продакшене берём URL из настроек приложения.
        redis_url: str
        if getattr(app_state, "redis_url", None):
            redis_url = str(app_state.redis_url)
        else:
            from core.config import get_settings as _get_settings

            redis_url = _get_settings().redis_url
        try:
            sub_client = aioredis.from_url(redis_url, decode_responses=True)
            own_sub_client = True
        except Exception as exc:
            logger.error("WS /ws/dashboard: не удалось создать Redis-клиент: %s", exc)
            await websocket.close(code=1011)
            return

    pubsub = sub_client.pubsub()
    heartbeat_task: asyncio.Task | None = None
    pubsub_task: asyncio.Task | None = None

    try:
        await pubsub.subscribe(*ALL_DASHBOARD_CHANNELS)
        logger.debug("WS /ws/dashboard: подписан на каналы %s", ALL_DASHBOARD_CHANNELS)
    except Exception as exc:
        logger.error("WS /ws/dashboard: не удалось подключиться к Redis pubsub: %s", exc)
        try:
            await pubsub.aclose()
        except Exception:
            pass
        if own_sub_client:
            try:
                await sub_client.aclose()
            except Exception:
                pass
        await websocket.close(code=1011)
        return

    async def _pubsub_loop() -> None:
        """Слушает Redis pubsub и отправляет события клиенту."""
        async for raw in pubsub.listen():
            if raw is None:
                continue
            if raw.get("type") != "message":
                continue
            channel: str = raw.get("channel", "")
            data: str = raw.get("data", "")
            event_type = _CHANNEL_TO_TYPE.get(channel, "unknown")
            try:
                payload = json.loads(data) if data else {}
            except json.JSONDecodeError:
                payload = {"raw": data}
            try:
                await websocket.send_text(_make_message(event_type, payload))
            except Exception:
                # Клиент отключился — выходим из цикла.
                return

    async def _heartbeat_loop() -> None:
        """Шлёт ping каждые _HEARTBEAT_SECONDS для keep-alive."""
        while True:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            try:
                await websocket.send_text(_make_message("ping", {}))
            except Exception:
                return

    try:
        pubsub_task = asyncio.create_task(_pubsub_loop())
        heartbeat_task = asyncio.create_task(_heartbeat_loop())

        # Ждём первый завершившийся таск; остальные отменяем.
        done, pending = await asyncio.wait(
            [pubsub_task, heartbeat_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        for task in done:
            exc = task.exception()
            if exc is not None:
                logger.debug("WS /ws/dashboard: задача завершилась с исключением: %s", exc)

    except WebSocketDisconnect:
        logger.info("WS /ws/dashboard: клиент отключился (WebSocketDisconnect)")
    except Exception as exc:
        logger.error("WS /ws/dashboard: неожиданная ошибка: %s", exc)
    finally:
        # Отменяем все живые таски.
        for task in (pubsub_task, heartbeat_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # Отписываемся и закрываем pubsub.
        try:
            await pubsub.unsubscribe(*ALL_DASHBOARD_CHANNELS)
            await pubsub.aclose()
        except Exception as exc:
            logger.debug("WS cleanup pubsub: %s", exc)

        # Закрываем sub_client только если создавали сами (не инжектированный).
        if own_sub_client:
            try:
                await sub_client.aclose()
            except Exception as exc:
                logger.debug("WS cleanup sub_client: %s", exc)

        logger.info("WS /ws/dashboard: cleanup завершён")
