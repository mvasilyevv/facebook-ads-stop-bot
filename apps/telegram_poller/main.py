# -*- coding: utf-8 -*-
"""Telegram Poller: long polling для обработки команд и callback-кнопок."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import signal
import sys
import time

from core.alerts.drain_worker import run_drain_loop
from core.alerts.queue import AlertQueue
from core.config import get_settings
from core.sentry import setup_sentry
from core.telegram.bot_handler import handle_update
from core.telegram.client import TelegramBotClient
from core.telegram.digest_scheduler import run_digest_scheduler
from core.telegram.service import (
    load_poller_offset,
    load_web_app_url,
    save_poller_offset,
    touch_poller_heartbeat,
)
from core.worker_utils import PidFileLock

logger = logging.getLogger(__name__)
TOKEN_RELOAD_INTERVAL_SECONDS = 30
ERROR_RETRY_DELAY_SECONDS = 3
_PID_FILE = pathlib.Path("/tmp/fb_telegram_poller.pid")

# Глобальный флаг для graceful shutdown
_shutdown_event: asyncio.Event | None = None


async def _register_bot_ui(client: TelegramBotClient) -> str:
    """Регистрирует список команд и web_app menu button.

    Возвращает URL, который был установлен (или пустую строку если не установлен).
    """
    commands = [
        {"command": "start", "description": "Главное меню"},
        {"command": "app", "description": "Открыть приложение"},
        {"command": "digest", "description": "Ежедневный дайджест за вчера"},
        {"command": "help", "description": "Справка"},
    ]
    # Админские команды супергруппы — показываем только администраторам.
    admin_commands = commands + [
        {"command": "init_topics", "description": "Создать форумные топики и привязать к стримам"},
        {
            "command": "bind_thread",
            "description": "Привязать текущий топик к стриму (WARNING/STOP/...)",
        },
    ]
    # Регистрируем для всех scope, иначе в группах команды не отображаются.
    for scope in (
        {"type": "default"},
        {"type": "all_private_chats"},
        {"type": "all_group_chats"},
    ):
        try:
            await client.set_my_commands(commands, scope=scope)
        except Exception:
            logger.exception(
                "Не удалось зарегистрировать команды (setMyCommands, scope=%s)",
                scope.get("type"),
            )
    # В admin-scope добавляем bind_thread / init_topics.
    try:
        await client.set_my_commands(admin_commands, scope={"type": "all_chat_administrators"})
    except Exception:
        logger.exception(
            "Не удалось зарегистрировать admin-команды (setMyCommands, scope=all_chat_administrators)"
        )

    web_app_url = await load_web_app_url()
    if not web_app_url:
        logger.warning("WEB_APP_URL не задан (ни в БД, ни в .env) — пропускаем setChatMenuButton")
        return ""
    if not web_app_url.startswith("https://"):
        logger.warning("WEB_APP_URL не https (%s) — пропускаем setChatMenuButton", web_app_url)
        return ""
    try:
        await client.set_chat_menu_button(web_app_url=web_app_url)
        return web_app_url
    except Exception:
        logger.exception("Не удалось установить chat menu button (setChatMenuButton)")
        return ""


async def _process_updates(
    client: TelegramBotClient,
    *,
    offset: int | None,
) -> int | None:
    """Обрабатывает пачку update и всегда сдвигает offset, даже если один update оказался битым."""
    updates = await client.get_updates(offset=offset)
    for update in updates:
        update_id = update.get("update_id")
        try:
            await handle_update(client, update)
        except Exception:
            logger.exception("Ошибка обработки update %s", update_id)
            cq_id = update.get("callback_query", {}).get("id")
            if cq_id:
                try:
                    await client.answer_callback_query(
                        cq_id, text="❌ Внутренняя ошибка — попробуйте ещё раз"
                    )
                except Exception:
                    logger.exception("Не удалось ответить на callback_query %s", cq_id)
        finally:
            if isinstance(update_id, int):
                offset = update_id + 1
    return offset


async def _load_bot_token() -> str:
    """Загружает bot_token из БД (расшифровывая) с fallback на .env."""
    from sqlalchemy import select

    from core.crypto import decrypt
    from core.db import get_session_factory
    from core.models import TelegramSettings

    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
            )
            row = result.scalar_one_or_none()
            if row and row.bot_token_encrypted:
                token = decrypt(row.bot_token_encrypted)
                if token:
                    logger.info("Telegram bot_token загружен из БД")
                    return token
    except Exception:
        logger.debug("Не удалось загрузить TG токен из БД", exc_info=True)

    settings = get_settings()
    return settings.telegram_bot_token


async def poller_runtime_loop(
    *,
    token_loader=_load_bot_token,
    client_factory=TelegramBotClient,
    offset_loader=load_poller_offset,
    offset_saver=save_poller_offset,
    reload_interval_seconds: int = TOKEN_RELOAD_INTERVAL_SECONDS,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Поддерживает живой poller, который ждёт появления токена и безопасно переживает ротацию."""
    stop = shutdown_event or _shutdown_event or asyncio.Event()
    try:
        offset: int | None = await offset_loader()
    except Exception:
        logger.exception("Не удалось загрузить offset Telegram poller-а")
        offset = None
    current_token = ""
    current_web_app_url: str = ""
    last_web_app_check: float = 0.0
    client: TelegramBotClient | None = None
    waiting_for_token_logged = False

    logger.info("Telegram poller запущен")

    try:
        while not stop.is_set():
            await touch_poller_heartbeat()

            token = (await token_loader()).strip()
            if not token:
                if client is not None:
                    await client.close()
                    client = None
                    current_token = ""
                    current_web_app_url = ""
                if not waiting_for_token_logged:
                    logger.info("Telegram poller ждёт bot_token и продолжает работать в фоне")
                    waiting_for_token_logged = True
                try:
                    await asyncio.wait_for(stop.wait(), timeout=reload_interval_seconds)
                except asyncio.TimeoutError:
                    pass
                continue

            if token != current_token or client is None:
                if client is not None:
                    await client.close()
                client = client_factory(token)
                current_token = token
                current_web_app_url = ""
                last_web_app_check = 0.0
                waiting_for_token_logged = False
                logger.info("Telegram poller подключён к актуальному bot_token")
                current_web_app_url = await _register_bot_ui(client)
                last_web_app_check = time.monotonic()

            now = time.monotonic()
            if now - last_web_app_check >= 60:
                last_web_app_check = now
                try:
                    new_url = await load_web_app_url()
                except Exception:
                    logger.debug("Не удалось перепроверить web_app_url", exc_info=True)
                    new_url = current_web_app_url
                if new_url != current_web_app_url:
                    logger.info(
                        "web_app_url изменён: %s → %s",
                        current_web_app_url or "(пусто)",
                        new_url or "(пусто)",
                    )
                    if new_url and new_url.startswith("https://"):
                        try:
                            await client.set_chat_menu_button(web_app_url=new_url)
                            current_web_app_url = new_url
                        except Exception:
                            logger.exception("Не удалось переустановить chat menu button")
                    else:
                        current_web_app_url = new_url

            try:
                new_offset = await _process_updates(client, offset=offset)
                if new_offset != offset:
                    offset = new_offset
                    await offset_saver(offset)
                await touch_poller_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка в runtime-цикле Telegram poller")
                await client.close()
                client = None
                current_token = ""
                current_web_app_url = ""
                try:
                    await asyncio.wait_for(stop.wait(), timeout=ERROR_RETRY_DELAY_SECONDS)
                except asyncio.TimeoutError:
                    pass
    finally:
        if client is not None:
            await client.close()
        logger.info("Telegram poller остановлен")


async def main() -> None:
    """Точка входа для Telegram poller с graceful shutdown."""
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown_event.set)

    settings = get_settings()
    setup_sentry(dsn=settings.sentry_dsn, environment=settings.sentry_environment)
    if not settings.telegram_chat_id:
        logger.info("TELEGRAM_CHAT_ID не задан — жду первое сообщение для определения...")

    with PidFileLock(_PID_FILE):
        # Запускаем drain-loop для Redis-очереди алёртов как background-task
        alert_queue = AlertQueue(redis_url=settings.redis_url)
        # Создаём временный клиент для drain-loop (токен может меняться, но для
        # доставки алёртов используем значение из настроек — не из БД)
        drain_client = TelegramBotClient(bot_token=settings.telegram_bot_token)
        drain_task = asyncio.create_task(
            run_drain_loop(alert_queue, drain_client),
            name="alerts-drain-loop",
        )

        # Запускаем планировщик daily digest если включён и задан chat_id
        digest_task = None
        if settings.digest_enabled and settings.telegram_chat_id:
            digest_client = TelegramBotClient(bot_token=settings.telegram_bot_token)
            digest_task = asyncio.create_task(
                run_digest_scheduler(
                    digest_client,
                    settings.telegram_chat_id,
                    tz=settings.digest_timezone,
                    hour=settings.digest_hour,
                ),
                name="digest-scheduler",
            )
        else:
            digest_client = None

        try:
            await poller_runtime_loop(shutdown_event=_shutdown_event)
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass
            await alert_queue.close()
            await drain_client.close()

            if digest_task is not None:
                digest_task.cancel()
                try:
                    await digest_task
                except asyncio.CancelledError:
                    pass
            if digest_client is not None:
                await digest_client.close()


if __name__ == "__main__":
    from core.logging import setup_logging

    setup_logging("telegram_poller")
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
