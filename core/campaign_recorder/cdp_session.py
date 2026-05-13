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


def _pick_target_page(browser) -> Page | None:
    """Ищет среди всех контекстов и вкладок ту, которая открыта в Ads Manager.

    Приоритет: adsmanager.facebook.com → business.facebook.com → facebook.com → первая.
    """
    candidates: list[Page] = []
    for ctx in browser.contexts:
        for p in ctx.pages:
            candidates.append(p)
    if not candidates:
        return None

    def score(p: Page) -> int:
        url = (p.url or "").lower()
        if "adsmanager.facebook.com" in url:
            return 100
        if "business.facebook.com" in url:
            return 50
        if "facebook.com" in url:
            return 10
        if url.startswith("about:") or url == "":
            return -10
        return 1

    candidates.sort(key=score, reverse=True)
    return candidates[0]


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

        # Playwright connect_over_cdp ждёт HTTP-URL и сам резолвит /json/version
        # в правильный ws://.../devtools/browser/<guid>. Голый ws:// без пути даёт 404.
        if cdp_url.startswith("ws://"):
            cdp_url = "http://" + cdp_url[len("ws://") :]
        elif cdp_url.startswith("wss://"):
            cdp_url = "https://" + cdp_url[len("wss://") :]

        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.connect_over_cdp(cdp_url)
            except Exception as exc:
                await client.close()
                raise CdpConnectionError(f"Не удалось подключиться к CDP {cdp_url}: {exc}") from exc

            logger.info("CDP контекстов в браузере: %d", len(browser.contexts))
            for ci, ctx in enumerate(browser.contexts):
                for pi, p in enumerate(ctx.pages):
                    logger.info(
                        "  ctx[%d] page[%d] url=%s frames=%d",
                        ci,
                        pi,
                        p.url,
                        len(p.frames),
                    )

            page = _pick_target_page(browser)
            if page is None:
                await browser.close()
                await client.close()
                raise CdpConnectionError("В браузере нет открытых вкладок")

            logger.info(
                "CDP подключён. Открыто вкладок: %d. Выбрана: %s",
                sum(len(c.pages) for c in browser.contexts),
                page.url,
            )
            try:
                yield page
            finally:
                await browser.close()
                await client.disconnect_browser()
                await client.close()
