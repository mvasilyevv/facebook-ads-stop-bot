from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import Page, async_playwright

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.config import get_settings

logger = logging.getLogger(__name__)


class CdpConnectionError(RuntimeError):
    """Не удалось подключиться к CDP Vision."""


def _make_browser_client() -> BrowserAgentClient:
    """Создаёт BrowserAgentClient из настроек приложения."""
    settings = get_settings()
    config = BrowserAgentConfig(
        vision_x_token=settings.vision_x_token,
        vision_api_url=settings.vision_api_url,
        vision_profile_id=settings.vision_profile_id,
    )
    return BrowserAgentClient(config)


class CdpSession:
    """Запускает Vision-профиль через gRPC и подключается к браузеру через CDP."""

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[Page, None]:
        """Стартует Vision-профиль, получает cdp_port, возвращает активную страницу."""
        client = _make_browser_client()
        try:
            await client.start()
            await client.start_browser()
        except Exception as exc:
            await client.close()
            raise CdpConnectionError(f"Не удалось запустить Vision-профиль: {exc}") from exc

        cdp_url = client.cdp_url
        if not cdp_url:
            await client.close()
            raise CdpConnectionError("Vision не вернул cdp_port после старта браузера")

        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.connect_over_cdp(cdp_url)
            except Exception as exc:
                await client.close()
                raise CdpConnectionError(
                    f"Не удалось подключиться к CDP {cdp_url}: {exc}"
                ) from exc

            contexts = browser.contexts
            if not contexts:
                await browser.close()
                await client.close()
                raise CdpConnectionError("CDP подключён, но нет открытых контекстов")

            pages = contexts[0].pages
            if not pages:
                await browser.close()
                await client.close()
                raise CdpConnectionError("Контекст есть, но нет открытых вкладок")

            page = pages[0]
            logger.info("CDP подключён через Vision: %s", page.url)
            try:
                yield page
            finally:
                await browser.close()
                await client.disconnect_browser()
                await client.close()
