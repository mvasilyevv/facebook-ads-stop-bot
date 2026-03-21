from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from apps.browser_host.adapters.models import AutomationLaunchResult

_CDP_CONNECT_MAX_ATTEMPTS = 3


def _load_async_playwright_factory() -> Callable[[], Any]:
    """Загружает фабрику Playwright или возвращает понятную русскую ошибку."""

    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Для подключения через Playwright нужно установить пакет `playwright`."
        ) from exc
    return async_playwright


@dataclass(slots=True, frozen=True)
class AttachedBrowserSession:
    profile_id: str
    cdp_url: str | None
    webdriver_url: str | None
    is_attached: bool
    browser: Any | None = None
    context: Any | None = None
    playwright: Any | None = None
    context_count: int = 0
    attached_at: datetime | None = None


class PlaywrightAttachService:
    """Подключает профиль браузера к Playwright по CDP."""

    def __init__(
        self,
        playwright_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._playwright_factory = playwright_factory

    async def attach(self, launch_result: AutomationLaunchResult) -> AttachedBrowserSession:
        logger = logging.getLogger(__name__)
        cdp_url = (launch_result.cdp_url or "").strip()
        if not cdp_url:
            raise RuntimeError(
                "Не удалось подключиться к браузеру: отсутствует CDP URL, "
                "WebDriver в этом сервисе не поддерживается."
            )

        playwright_factory = self._playwright_factory or _load_async_playwright_factory()
        try:
            playwright_manager = playwright_factory()
            playwright = await playwright_manager.start()
        except RuntimeError:
            raise
        except Exception as exc:  # pragma: no cover - зависит от окружения Playwright
            raise RuntimeError(
                "Не удалось запустить Playwright Python для подключения к браузеру."
            ) from exc

        last_error: Exception | None = None
        browser = None
        for attempt in range(_CDP_CONNECT_MAX_ATTEMPTS):
            try:
                browser = await playwright.chromium.connect_over_cdp(cdp_url)
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < _CDP_CONNECT_MAX_ATTEMPTS - 1:
                    delay = 2**attempt
                    logger.warning(
                        "Попытка %s/%s подключения к CDP %s не удалась, жду %sс: %s",
                        attempt + 1,
                        _CDP_CONNECT_MAX_ATTEMPTS,
                        cdp_url,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
        if browser is None:
            with contextlib.suppress(Exception):
                await playwright.stop()
            raise RuntimeError(
                f"Не удалось подключиться к CDP по адресу {cdp_url} "
                f"за {_CDP_CONNECT_MAX_ATTEMPTS} попыток: {last_error}"
            ) from last_error

        contexts = list(getattr(browser, "contexts", []) or [])
        logger.info(
            "Playwright подключился к профилю %s по CDP %s", launch_result.profile_id, cdp_url
        )
        return AttachedBrowserSession(
            profile_id=launch_result.profile_id,
            cdp_url=launch_result.cdp_url,
            webdriver_url=launch_result.webdriver_url,
            is_attached=True,
            browser=browser,
            context=contexts[0] if contexts else None,
            playwright=playwright,
            context_count=len(contexts),
            attached_at=datetime.now(tz=UTC),
        )

    async def detach(self, session: AttachedBrowserSession) -> None:
        """Отключает локальный Playwright-клиент, не останавливая сам профиль браузера."""

        logger = logging.getLogger(__name__)
        if session.playwright is None:
            return
        with contextlib.suppress(Exception):
            await session.playwright.stop()
        logger.info("Playwright-клиент отключен для профиля %s", session.profile_id)
