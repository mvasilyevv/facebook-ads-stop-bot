# -*- coding: utf-8 -*-
"""Точка входа: запускает observer worker с подключением к Node.js browser-agent через gRPC."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from core.config import get_settings
from core.observer.runtime_status import (
    format_observer_runtime_message,
    update_observer_runtime_status,
)
from core.sentry import setup_sentry

_PID_FILE = "/tmp/fb_observer.pid"


def _acquire_pid_lock() -> None:
    """Проверяет, что нет другого запущенного observer'а. Иначе — выходим."""
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            logging.getLogger(__name__).error(
                "Observer уже запущен (PID %s). Запуск второго экземпляра запрещён.", old_pid
            )
            sys.exit(1)
        except (ValueError, ProcessLookupError, OSError):
            pass
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _release_pid_lock() -> None:
    """Удаляет PID-файл при завершении."""
    try:
        os.unlink(_PID_FILE)
    except FileNotFoundError:
        pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
VISION_SETTINGS_POLL_INTERVAL_SECONDS = 5


async def _wait_for_shutdown_or_timeout(
    shutdown_event: asyncio.Event,
    timeout_seconds: int,
) -> bool:
    """Ждёт таймаут или сигнал остановки."""
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=timeout_seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def main() -> None:
    """Запуск observer worker через gRPC к browser-agent."""
    _s = get_settings()
    setup_sentry(dsn=_s.sentry_dsn, environment=_s.sentry_environment)
    _acquire_pid_lock()
    await update_observer_runtime_status(
        status="STARTING",
        message="Воркер запускается и проверяет настройки подключения.",
    )
    settings = get_settings()
    from apps.observer_worker.main import (
        _scan_guard,
        observer_loop,
    )
    from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
    from core.observer.db_queries import (
        check_scanning_enabled,
        load_ad_states_from_db,
        load_offers_from_db,
        load_vision_settings_for_runtime,
    )

    # Graceful shutdown
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        logger.info("Получен сигнал остановки — завершаем работу")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    waiting_for_vision_logged = False

    try:
        while not shutdown_event.is_set():
            if not await check_scanning_enabled():
                await update_observer_runtime_status(
                    status="PAUSED",
                    message="Сканирование выключено в настройках.",
                    clear_last_error=True,
                )
                if await _wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
                continue

            (
                vision_x_token,
                vision_api_url,
                vision_profile_id,
            ) = await load_vision_settings_for_runtime(
                fallback_x_token=settings.vision_x_token,
                fallback_api_url=settings.vision_api_url,
                fallback_profile_id=settings.vision_profile_id,
            )
            if not vision_x_token or not vision_profile_id:
                await update_observer_runtime_status(
                    status="WAITING_CONFIG",
                    message="Ожидаем Vision X-Token и profile_id в настройках Vision.",
                )
                if not waiting_for_vision_logged:
                    logger.info(
                        "Observer ждёт Vision-настройки из UI или .env и продолжает работать в фоне"
                    )
                    waiting_for_vision_logged = True
                if await _wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
                continue

            waiting_for_vision_logged = False
            await update_observer_runtime_status(
                status="CONNECTING",
                message="Подключаемся к browser-agent и готовим браузер.",
            )

            # Подключаемся к browser-agent через gRPC
            grpc_config = BrowserAgentConfig(
                vision_x_token=vision_x_token,
                vision_api_url=vision_api_url,
                vision_profile_id=vision_profile_id,
            )
            grpc_client = BrowserAgentClient(grpc_config)

            try:
                await grpc_client.start()
                await grpc_client.start_browser()
                logger.info("Подключён к browser-agent, session_id=%s", grpc_client.session_id)

                await update_observer_runtime_status(
                    status="RUNNING",
                    message="Подключение к browser-agent установлено. Воркер готов к циклу сканирования.",
                    clear_last_error=True,
                )

                offers = await load_offers_from_db()
                ad_states = await load_ad_states_from_db()
                _scan_guard.initialize_from_count(len(ad_states))

                await observer_loop(
                    grpc_client=grpc_client,
                    offers=offers,
                    telegram_bot_token=settings.telegram_bot_token,
                    telegram_chat_id=settings.telegram_chat_id,
                    shutdown_event=shutdown_event,
                )
                logger.info("Observer цикл завершён")
            except KeyboardInterrupt:
                logger.info("Остановка по Ctrl+C")
                break
            except Exception as exc:
                if shutdown_event.is_set():
                    break
                await update_observer_runtime_status(
                    status="ERROR",
                    message=format_observer_runtime_message(exc),
                    last_error=format_observer_runtime_message(exc),
                )
                logger.exception("Observer: ошибка запуска или подключения")
                if await _wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
            finally:
                try:
                    await grpc_client.disconnect_browser()
                except Exception:
                    logger.debug("Observer: не удалось отключиться от browser-agent", exc_info=True)
                try:
                    await grpc_client.close()
                except Exception:
                    logger.debug("Observer: не удалось закрыть gRPC канал", exc_info=True)
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    finally:
        await update_observer_runtime_status(
            status="STOPPED",
            message="Воркер остановлен.",
        )
        _release_pid_lock()
        logger.info("Ресурсы освобождены")


if __name__ == "__main__":
    asyncio.run(main())
