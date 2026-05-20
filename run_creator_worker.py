# -*- coding: utf-8 -*-
"""Точка входа: Creator worker — поллит PlanRun, исполняет через CreatorService gRPC."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import signal
import sys

from sqlalchemy import func, select

from apps.creator_worker.main import _heartbeat_loop, creator_worker_loop
from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.config import get_settings
from core.crypto import decrypt
from core.db import get_session_factory
from core.domain import PlanRunStatus
from core.models import PlanRun, VisionSettings
from core.sentry import setup_sentry
from core.worker_utils import PidFileLock, wait_for_shutdown_or_timeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

VISION_SETTINGS_POLL_INTERVAL_SECONDS = 2
CREATOR_POLL_INTERVAL_SECONDS = 3
GRPC_DISCONNECT_TIMEOUT_SECONDS = 15
GRPC_CLOSE_TIMEOUT_SECONDS = 10


async def _load_vision_settings() -> tuple[str, str, str]:
    """Загружает Vision-настройки из БД с fallback на .env."""
    settings = get_settings()
    factory = get_session_factory()
    try:
        async with factory() as session:
            row = await session.scalar(
                select(VisionSettings).where(VisionSettings.singleton_key == "default")
            )
            if row and row.x_token_encrypted and row.profile_id:
                token = decrypt(row.x_token_encrypted)
                if token:
                    return token, row.api_url or settings.vision_api_url, row.profile_id
    except Exception:
        logger.debug("Не удалось загрузить Vision-настройки из БД", exc_info=True)
    return settings.vision_x_token, settings.vision_api_url, settings.vision_profile_id


def _build_client_config(token: str, url: str, profile: str) -> BrowserAgentConfig:
    """Создаёт BrowserAgentConfig из Vision-настроек."""
    settings = get_settings()
    return BrowserAgentConfig(
        vision_x_token=token,
        vision_api_url=url,
        vision_profile_id=profile,
        vision_folder_id=getattr(settings, "vision_folder_id", None),
    )


async def _init_grpc_client(token: str, url: str, profile: str) -> BrowserAgentClient:
    """Создаёт и подключает gRPC клиент к browser-agent."""
    client = BrowserAgentClient(_build_client_config(token, url, profile))
    await client.start()
    await client.start_browser()
    logger.info("Creator worker: gRPC клиент подключён, session_id=%s", client.session_id)
    return client


async def _close_grpc_client(client: BrowserAgentClient | None) -> None:
    """Закрывает gRPC клиент с таймаутами."""
    if client is None:
        return
    try:
        await asyncio.wait_for(client.disconnect_browser(), timeout=GRPC_DISCONNECT_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, Exception):
        logger.debug("Creator worker: не удалось отключиться от browser-agent", exc_info=True)
    try:
        await asyncio.wait_for(client.close(), timeout=GRPC_CLOSE_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, Exception):
        logger.debug("Creator worker: не удалось закрыть gRPC канал", exc_info=True)


async def _has_queued_plan_runs() -> bool:
    """Проверяет наличие QUEUED PlanRun в БД."""
    factory = get_session_factory()
    async with factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(PlanRun).where(PlanRun.status == PlanRunStatus.QUEUED)
        )
    return bool(count)


async def main() -> None:
    """Основной цикл Creator worker."""
    settings = get_settings()
    setup_sentry(dsn=settings.sentry_dsn, environment=settings.sentry_environment)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)
    loop.add_signal_handler(signal.SIGINT, shutdown_event.set)

    status_ref: list[str] = ["idle"]
    message_ref: list[str | None] = [None]
    heartbeat_task = asyncio.create_task(_heartbeat_loop(status_ref, message_ref))
    waiting_for_vision_logged = False

    try:
        while not shutdown_event.is_set():
            if not await _has_queued_plan_runs():
                if await wait_for_shutdown_or_timeout(
                    shutdown_event, CREATOR_POLL_INTERVAL_SECONDS
                ):
                    break
                continue

            vision_token, vision_url, vision_profile = await _load_vision_settings()
            if not vision_token or not vision_profile:
                if not waiting_for_vision_logged:
                    logger.info("Creator worker ждёт Vision-настройки из UI или .env")
                    waiting_for_vision_logged = True
                if await wait_for_shutdown_or_timeout(
                    shutdown_event, VISION_SETTINGS_POLL_INTERVAL_SECONDS
                ):
                    break
                continue

            waiting_for_vision_logged = False
            grpc_client: BrowserAgentClient | None = None
            try:
                grpc_client = await _init_grpc_client(vision_token, vision_url, vision_profile)
                await creator_worker_loop(
                    poll_interval_seconds=CREATOR_POLL_INTERVAL_SECONDS,
                    grpc_client=grpc_client,
                    shutdown_event=shutdown_event,
                    status_ref=status_ref,
                    message_ref=message_ref,
                    telegram_token=settings.telegram_bot_token,
                    telegram_chat_id=settings.telegram_chat_id,
                )
            except KeyboardInterrupt:
                logger.info("Creator worker остановлен по Ctrl+C")
                break
            except Exception:
                if shutdown_event.is_set():
                    break
                logger.exception("Creator worker: ошибка цикла, перезапуск через таймаут")
                if await wait_for_shutdown_or_timeout(
                    shutdown_event, VISION_SETTINGS_POLL_INTERVAL_SECONDS
                ):
                    break
            finally:
                await _close_grpc_client(grpc_client)
    except KeyboardInterrupt:
        logger.info("Creator worker остановлен по Ctrl+C")
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        logger.info("Creator worker: ресурсы освобождены")


if __name__ == "__main__":
    _PID_FILE = pathlib.Path("/tmp/fb_creator_worker.pid")
    try:
        with PidFileLock(_PID_FILE):
            asyncio.run(main())
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
