from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import Page, async_playwright

logger = logging.getLogger(__name__)


class CdpConnectionError(RuntimeError):
    """Не удалось подключиться к CDP Vision."""


class CdpSession:
    """Подключение к уже запущенному Vision-профилю через CDP."""

    def __init__(self, cdp_url: str) -> None:
        self._cdp_url = cdp_url

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[Page, None]:
        """Подключается через CDP и возвращает активную страницу."""
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.connect_over_cdp(self._cdp_url)
            except Exception as exc:
                raise CdpConnectionError(
                    f"Не удалось подключиться к CDP по адресу {self._cdp_url}: {exc}"
                ) from exc

            contexts = browser.contexts
            if not contexts:
                raise CdpConnectionError("CDP подключён, но нет открытых контекстов")

            pages = contexts[0].pages
            if not pages:
                raise CdpConnectionError("Контекст есть, но нет открытых вкладок")

            page = pages[0]
            logger.info("CDP подключён: %s", page.url)
            try:
                yield page
            finally:
                await browser.close()
