# -*- coding: utf-8 -*-
"""Точка входа: запускает observer worker с подключением к Vision браузеру."""

from __future__ import annotations

from core.browser.stealth import patch_patchright

patch_patchright()

import asyncio
import logging
import os
import signal
import sys

from core.browser.manager import VisionBrowserManager
from core.browser.vision_client import VisionClient
from core.config import get_settings
from core.observer.runtime_status import (
    format_observer_runtime_message,
    update_observer_runtime_status,
)
from core.scanner.parser import parse_ads_from_page
from core.sentry import setup_sentry

_PID_FILE = "/tmp/fb_observer.pid"


def _acquire_pid_lock() -> None:
    """Проверяет, что нет другого запущенного observer'а. Иначе — выходим."""
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            # Проверяем, жив ли процесс
            os.kill(old_pid, 0)
            logging.getLogger(__name__).error(
                "Observer уже запущен (PID %s). Запуск второго экземпляра запрещён.", old_pid
            )
            sys.exit(1)
        except (ValueError, ProcessLookupError, OSError):
            # Устаревший lock-файл — удаляем
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
    """Запуск observer worker с подключением к Vision anti-detect."""
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
    from core.observer.db_queries import (
        load_ad_states_from_db,
        load_offers_from_db,
        load_vision_settings_for_runtime,
    )

    # Graceful shutdown через asyncio event loop сигналы
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
                message="Подключаемся к профилю Vision и готовим браузер.",
            )
            vision = VisionClient(
                x_token=vision_x_token,
                base_url=vision_api_url,
            )
            manager = VisionBrowserManager(
                vision_client=vision,
                profile_id=vision_profile_id,
            )

            try:
                await manager.connect()
                page = await manager.get_page()
                logger.info("Подключён к Vision. Текущий URL: %s", page.url)
                await update_observer_runtime_status(
                    status="RUNNING",
                    message="Подключение к Vision установлено. Воркер готов к циклу сканирования.",
                    clear_last_error=True,
                )

                offers = await load_offers_from_db()
                ad_states = await load_ad_states_from_db()
                _scan_guard.initialize_from_count(len(ad_states))
                await observer_loop(
                    page=page,
                    offers=offers,
                    telegram_bot_token=settings.telegram_bot_token,
                    telegram_chat_id=settings.telegram_chat_id,
                    parse_fn=parse_ads_from_page,
                    browser_manager=manager,
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
                logger.exception("Observer: ошибка запуска или подключения к Vision")
                if await _wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
            finally:
                try:
                    await manager.disconnect()
                except Exception:
                    logger.debug("Observer: не удалось корректно отключить браузер", exc_info=True)
                try:
                    await vision.close()
                except Exception:
                    logger.debug("Observer: не удалось закрыть Vision клиент", exc_info=True)
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
