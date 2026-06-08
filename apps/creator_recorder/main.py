# -*- coding: utf-8 -*-
"""creator_recorder main loop.

Pubsub-triggered воркер. Принимает события на запись планов через Redis:
  fb_agent:creator:record_start  → StartRecording в CreatorService
  fb_agent:creator:record_stop   → StopRecording + INSERT в creator_plans

Состояние процесса:
- heartbeat: Redis worker:heartbeat:creator_recorder TTL 60s
- pubsub_loop: подписка на оба канала, диспетчер
- graceful: SIGTERM/SIGINT → stop event → закрыть pubsub, gRPC, engine

TG-интеграция (/record_plan, /stop_record, /plans) реализована в
`core/telegram/handlers/creator.py` и публикует в те же каналы record_start/stop.
Pubsub остаётся транспортом: recorder-consumer поднимается отдельным процессом.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from typing import Any

import httpx
import redis.asyncio as redis_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.telegram import format as fmt
from core.telegram.client import TelegramBotClient

logger = logging.getLogger("creator_recorder")

WORKER_NAME = "creator_recorder"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

CHANNEL_RECORD_START = "fb_agent:creator:record_start"
CHANNEL_RECORD_STOP = "fb_agent:creator:record_stop"
CHANNELS: tuple[str, ...] = (CHANNEL_RECORD_START, CHANNEL_RECORD_STOP)

_MAX_NAME_RETRIES = 5


# ====================== config helpers ======================


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


def _build_browser_client() -> BrowserAgentClient:
    from core.config import get_settings

    settings = get_settings()
    config = BrowserAgentConfig(
        grpc_host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        grpc_port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
        vision_x_token=settings.vision_x_token,
        vision_api_url=settings.vision_api_url,
        vision_profile_id=settings.vision_profile_id,
    )
    return BrowserAgentClient(config)


# ====================== handlers ======================


async def handle_record_start(
    client: BrowserAgentClient,
    payload: dict[str, Any],
) -> bool:
    """Стартует recorder в браузере. payload должен содержать plan_name."""
    plan_name = str(payload.get("plan_name") or "untitled")[:255]
    try:
        started, message = await client.start_recording(plan_name)
    except Exception:  # noqa: BLE001
        logger.exception("recorder: StartRecording бросил исключение")
        return False
    if started:
        logger.info("recorder: запись плана '%s' стартовала: %s", plan_name, message)
    else:
        logger.warning("recorder: StartRecording отказал для '%s': %s", plan_name, message)
    return started


async def handle_record_stop(
    client: BrowserAgentClient,
    engine: AsyncEngine,
    payload: dict[str, Any],
    tg_client: TelegramBotClient | None = None,
) -> str | None:
    """Останавливает запись и сохраняет план в creator_plans.

    Возвращает UUID созданной записи или None.
    После успешного INSERT — отправляет TG-confirmation если передан tg_client.
    """
    try:
        stopped, plan_json, recorded_steps = await client.stop_recording()
    except Exception:  # noqa: BLE001
        logger.exception("recorder: StopRecording бросил исключение")
        return None
    if not stopped:
        logger.warning("recorder: StopRecording отказал")
        return None
    if not plan_json or recorded_steps == 0:
        logger.warning("recorder: пустой план (steps=%d), не сохраняю", recorded_steps)
        return None

    try:
        plan_data = json.loads(plan_json)
    except json.JSONDecodeError as exc:
        logger.error("recorder: невалидный plan_json: %s", exc)
        return None

    steps = plan_data.get("steps") or plan_data.get("actions") or []
    schema_version = int(plan_data.get("schema_version") or 1)
    variables = plan_data.get("variables") or {}

    name_hint = str(payload.get("plan_name") or plan_data.get("name") or "recorded")[:200]
    requested_by = str(payload.get("requested_by") or "creator_recorder")[:64]

    plan_id = await _insert_plan(
        engine,
        name=name_hint,
        schema_version=schema_version,
        steps=steps,
        variables=variables,
        created_by=requested_by,
    )
    if plan_id:
        logger.info(
            "recorder: план '%s' (id=%s) сохранён со %d шагами",
            name_hint,
            plan_id,
            recorded_steps,
        )
        # TG-confirmation получателю (recipient_id из pubsub-payload)
        recipient_id = str(payload.get("recipient_id") or "").strip()
        if tg_client and recipient_id:
            try:
                await tg_client.send_message(
                    chat_id=recipient_id,
                    text=(
                        f"✅ План {fmt.b(name_hint)} сохранён ({fmt.code(f'id={plan_id}')}).\n"
                        "Запусти его через /plans."
                    ),
                    parse_mode="HTML",
                )
            except Exception:  # noqa: BLE001
                logger.warning("recorder: не удалось отправить TG-confirmation", exc_info=True)
    return plan_id


async def _insert_plan(
    engine: AsyncEngine,
    *,
    name: str,
    schema_version: int,
    steps: list[dict[str, Any]],
    variables: dict[str, Any],
    created_by: str,
) -> str | None:
    """INSERT в creator_plans. При конфликте имени — добавляем timestamp-suffix."""
    final_name = name
    for attempt in range(_MAX_NAME_RETRIES):
        try:
            async with engine.begin() as conn:
                row = (
                    await conn.execute(
                        text(
                            """
                            INSERT INTO creator_plans
                                (name, schema_version, steps, variables,
                                 description, created_by, is_archived)
                            VALUES
                                (:n, :v, CAST(:s AS JSONB), CAST(:vars AS JSONB),
                                 NULL, :cb, false)
                            RETURNING id
                            """
                        ),
                        {
                            "n": final_name[:255],
                            "v": int(schema_version),
                            "s": json.dumps(steps, ensure_ascii=False),
                            "vars": json.dumps(variables, ensure_ascii=False),
                            "cb": created_by,
                        },
                    )
                ).first()
            if row:
                return str(row[0])
        except IntegrityError as exc:
            if "creator_plans" in str(exc).lower() or "uq_creator_plans" in str(exc).lower():
                suffix = datetime.now(timezone.utc).strftime("_%Y%m%dT%H%M%S")
                final_name = f"{name[:200]}{suffix}"
                if attempt < _MAX_NAME_RETRIES - 1:
                    logger.warning("recorder: имя '%s' занято, пробую '%s'", name, final_name)
                    continue
            logger.exception("recorder: не удалось сохранить план")
            return None
        except Exception:  # noqa: BLE001
            logger.exception("recorder: не удалось сохранить план")
            return None
    return None


# ====================== pubsub loop ======================


async def _process_message(
    channel: str,
    raw_data: Any,
    *,
    client: BrowserAgentClient,
    engine: AsyncEngine,
    tg_client: TelegramBotClient | None = None,
) -> None:
    """Разобрать payload и вызвать соответствующий handler."""
    try:
        payload = json.loads(raw_data) if raw_data else {}
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("recorder: нечитаемый payload в %s: %r", channel, raw_data)
        return

    try:
        if channel == CHANNEL_RECORD_START:
            await handle_record_start(client, payload)
        elif channel == CHANNEL_RECORD_STOP:
            await handle_record_stop(client, engine, payload, tg_client=tg_client)
        else:
            logger.debug("recorder: неизвестный канал %s", channel)
    except Exception:  # noqa: BLE001
        logger.exception("recorder: ошибка обработки сообщения из %s", channel)


async def pubsub_loop(
    redis_client: redis_asyncio.Redis,
    engine: AsyncEngine,
    client: BrowserAgentClient,
    stop: asyncio.Event,
    *,
    poll_timeout: float = 1.0,
    tg_client: TelegramBotClient | None = None,
) -> None:
    """Подписка на каналы recorder + диспетчер. Завершается по stop event."""
    pubsub = redis_client.pubsub()
    try:
        await pubsub.subscribe(*CHANNELS)
        logger.info("recorder: подписан на каналы %s", ", ".join(CHANNELS))
        while not stop.is_set():
            try:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=poll_timeout,
                )
            except Exception:  # noqa: BLE001
                logger.exception("recorder: ошибка чтения pubsub")
                await asyncio.sleep(poll_timeout)
                continue
            if msg is None:
                continue
            channel = msg.get("channel")
            if isinstance(channel, bytes):
                channel = channel.decode("utf-8", errors="replace")
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            await _process_message(
                channel or "",
                data,
                client=client,
                engine=engine,
                tg_client=tg_client,
            )
    finally:
        try:
            await pubsub.unsubscribe(*CHANNELS)
        except Exception:  # noqa: BLE001
            pass
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


async def heartbeat_loop(redis_client: redis_asyncio.Redis, stop: asyncio.Event) -> None:
    """Периодически обновляет worker:heartbeat:creator_recorder с TTL 60s."""
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ====================== entrypoint ======================


async def _build_tg_client(engine: AsyncEngine) -> TelegramBotClient | None:
    """Построить TelegramBotClient из telegram_config в БД. None если не сконфигурирован."""
    try:
        from core.telegram.service import load_telegram_config

        cfg = await load_telegram_config(engine)
        if not cfg or not cfg.bot_token:
            logger.warning("recorder: telegram_config пуст, TG-confirmation недоступен")
            return None
        http_client = httpx.AsyncClient(timeout=15.0)
        return TelegramBotClient(bot_token=cfg.bot_token, http_client=http_client)
    except Exception:  # noqa: BLE001
        logger.warning("recorder: не удалось построить TelegramBotClient", exc_info=True)
        return None


async def main_loop(database_url: str | None = None) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, echo=False)
    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)
    browser_client = _build_browser_client()
    await browser_client.start()

    # TelegramBotClient для confirmation после INSERT
    tg_client = await _build_tg_client(engine)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("creator_recorder запущен")
    try:
        await asyncio.gather(
            pubsub_loop(redis_client, engine, browser_client, stop, tg_client=tg_client),
            heartbeat_loop(redis_client, stop),
        )
    finally:
        try:
            await browser_client.close()
        except Exception:  # noqa: BLE001
            logger.exception("browser_client.close() упал")
        if tg_client is not None:
            try:
                await tg_client.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            await redis_client.aclose()
        except Exception:  # noqa: BLE001
            pass
        await engine.dispose()
        logger.info("creator_recorder остановлен")


__all__ = [
    "WORKER_NAME",
    "HEARTBEAT_KEY",
    "CHANNEL_RECORD_START",
    "CHANNEL_RECORD_STOP",
    "CHANNELS",
    "handle_record_start",
    "handle_record_stop",
    "_process_message",
    "pubsub_loop",
    "heartbeat_loop",
    "main_loop",
]
