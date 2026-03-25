# -*- coding: utf-8 -*-
"""Менеджер Playwright-сессий через Vision anti-detect браузер.

Подключается к запущенному профилю Vision по CDP и предоставляет
Playwright Page для observer worker и disable worker.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from playwright.async_api import Browser, Page, async_playwright

from core.browser.vision_client import VisionClient

logger = logging.getLogger(__name__)


class VisionBrowserManager:
    """Управляет подключением к Vision браузеру через Playwright CDP."""

    def __init__(
        self,
        vision_client: VisionClient,
        profile_id: str,
        folder_id: str | None = None,
    ) -> None:
        self._vision = vision_client
        self._folder_id = folder_id  # будет авто-определён если None
        self._profile_id = profile_id
        self._playwright = None
        self._browser: Browser | None = None

    async def connect(self) -> Browser:
        """Запускает профиль в Vision и подключается через CDP.

        Возвращает Playwright Browser, уже подключённый к anti-detect профилю.
        """
        # Авто-определяем folder_id если не задан
        if self._folder_id is None:
            self._folder_id = await self._vision.resolve_folder_id(
                self._profile_id
            )

        # Запускаем профиль через Vision API
        profile = await self._vision.start_profile(
            self._folder_id,
            self._profile_id,
        )

        if profile.port is None:
            raise RuntimeError(
                f"Vision не вернул CDP-порт для профиля {self._profile_id}"
            )

        cdp_url = self._vision.cdp_url(profile.port)
        logger.info("Подключение через CDP: %s", cdp_url)

        # Подключаемся через Playwright
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)

        logger.info(
            "Подключён к Vision профилю %s, контекстов: %s",
            self._profile_id,
            len(self._browser.contexts),
        )
        return self._browser

    async def get_page(self) -> Page:
        """Возвращает активную страницу или создаёт новую.

        Использует первый контекст (это контекст anti-detect профиля).
        """
        if self._browser is None:
            await self.connect()

        assert self._browser is not None
        contexts = self._browser.contexts
        if not contexts:
            raise RuntimeError("Нет доступных контекстов в браузере")

        context = contexts[0]
        pages = context.pages
        if pages:
            return pages[0]

        # Создаём новую страницу в контексте anti-detect профиля
        return await context.new_page()

    async def disconnect(self) -> None:
        """Отключается от браузера (не останавливает профиль)."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("Ошибка при закрытии browser", exc_info=True)
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("Ошибка при остановке Playwright", exc_info=True)
            self._playwright = None

    async def stop_profile(self) -> None:
        """Останавливает профиль в Vision."""
        await self.disconnect()
        await self._vision.stop_profile(self._folder_id, self._profile_id)

    @asynccontextmanager
    async def session(self):
        """Контекстный менеджер: подключение → page → отключение."""
        try:
            await self.connect()
            page = await self.get_page()
            yield page
        finally:
            await self.disconnect()
