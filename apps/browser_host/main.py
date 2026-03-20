from __future__ import annotations

import asyncio
import logging

from apps.browser_host.adapters.factory import build_adapter
from apps.browser_host.playwright_attach import PlaywrightAttachService
from apps.browser_host.session_manager import BrowserSessionManager
from core.config import get_settings
from core.logging import configure_logging


async def run_browser_host() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    settings = get_settings()
    manager = BrowserSessionManager(
        adapter=build_adapter(settings),
        playwright_attach_service=PlaywrightAttachService(),
    )
    health = await manager.healthcheck()
    if health.is_healthy:
        logger.info(
            "Browser host запущен с вендором %s: %s", settings.browser_vendor, health.message
        )
    else:
        logger.warning(
            "Browser host запущен с вендором %s, но пока не готов: %s",
            settings.browser_vendor,
            health.message,
        )
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(run_browser_host())


if __name__ == "__main__":
    main()
