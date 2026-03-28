# -*- coding: utf-8 -*-
"""Менеджер Patchright-сессий через Vision anti-detect браузер.

Подключается к запущенному профилю Vision по CDP и предоставляет
Patchright Page для observer worker и disable worker.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from patchright.async_api import Browser, Page, async_playwright

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
            self._folder_id = await self._vision.resolve_folder_id(self._profile_id)

        # Запускаем профиль через Vision API
        profile = await self._vision.start_profile(
            self._folder_id,
            self._profile_id,
        )

        # Если порт не вернулся — профиль был запущен без CDP, перезапускаем
        if profile.port is None:
            logger.info(
                "CDP-порт не получен, перезапускаю профиль %s",
                self._profile_id,
            )
            await self._vision.stop_profile(self._folder_id, self._profile_id)
            import asyncio
            await asyncio.sleep(2)
            profile = await self._vision.start_profile(
                self._folder_id,
                self._profile_id,
            )

        if profile.port is None:
            raise RuntimeError(f"Vision не вернул CDP-порт для профиля {self._profile_id}")

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

    async def get_page(self, url_hint: str = "facebook.com") -> Page:
        """Возвращает страницу браузера, подходящую по URL.

        Ищет вкладку содержащую url_hint (по умолчанию 'facebook.com').
        Если не найдена — возвращает первую не-devtools страницу.
        Если вообще нет страниц — создаёт новую.

        Args:
            url_hint: подстрока для поиска нужной вкладки.
        """
        if self._browser is None:
            await self.connect()

        assert self._browser is not None
        contexts = self._browser.contexts
        if not contexts:
            raise RuntimeError("Нет доступных контекстов в браузере")

        all_pages = [p for ctx in contexts for p in ctx.pages]

        # 1. Ищем вкладку с url_hint
        for p in all_pages:
            if url_hint in (p.url or ""):
                return p

        # 2. Fallback: первая страница не являющаяся devtools/chrome-extension
        for p in all_pages:
            url = p.url or ""
            if not url.startswith("devtools://") and not url.startswith("chrome-extension://"):
                return p

        # 3. Создаём новую страницу
        return await contexts[0].new_page()

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
