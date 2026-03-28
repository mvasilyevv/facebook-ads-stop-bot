# -*- coding: utf-8 -*-
"""Точка входа: запускает observer worker с подключением к Vision браузеру."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

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

from core.browser.manager import VisionBrowserManager
from core.browser.vision_client import VisionClient
from core.config import get_settings
from core.scanner.parser import parse_ads_from_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Запуск observer worker с подключением к Vision anti-detect."""
    _acquire_pid_lock()
    settings = get_settings()

    if not settings.vision_x_token:
        logger.error("Не задан VISION_X_TOKEN в .env")
        sys.exit(1)

    if not settings.vision_profile_id:
        logger.error("Не задан VISION_PROFILE_ID в .env")
        sys.exit(1)

    # Инициализация Vision клиента
    vision = VisionClient(
        x_token=settings.vision_x_token,
        base_url=settings.vision_api_url,
    )

    # Менеджер браузера (folder_id определится автоматически через API)
    manager = VisionBrowserManager(
        vision_client=vision,
        profile_id=settings.vision_profile_id,
    )

    # Graceful shutdown через asyncio event loop сигналы
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        logger.info("Получен сигнал остановки — завершаем работу")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        # Подключаемся к Vision
        await manager.connect()
        page = await manager.get_page()

        logger.info("Подключён к Vision. Текущий URL: %s", page.url)

        # Загружаем офферы из БД
        from apps.observer_worker.main import load_offers_from_db, observer_loop

        offers = await load_offers_from_db()

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
    finally:
        await manager.disconnect()
        await vision.close()
        _release_pid_lock()
        logger.info("Ресурсы освобождены")


if __name__ == "__main__":
    asyncio.run(main())
