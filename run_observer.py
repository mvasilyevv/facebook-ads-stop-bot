# -*- coding: utf-8 -*-
"""Точка входа: запускает observer worker с подключением к Vision браузеру."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from decimal import Decimal

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

    # Обработка graceful shutdown
    shutdown_event = asyncio.Event()

    def _handle_signal(signum, frame):
        logger.info("Получен сигнал %s — завершаем работу", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

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
            interval_seconds=settings.default_observer_interval_seconds,
            warning_percent_of_stop=Decimal("80"),
            parse_fn=parse_ads_from_page,
        )

    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    finally:
        await manager.disconnect()
        await vision.close()
        logger.info("Ресурсы освобождены")


if __name__ == "__main__":
    asyncio.run(main())
