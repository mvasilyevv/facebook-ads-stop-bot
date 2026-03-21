from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from apps.browser_host.playwright_attach import AttachedBrowserSession
from apps.browser_host.session_manager import BrowserSessionManager
from core.actions import BrowserActionResult

_PAUSE_BUTTON_NAMES = ("Пауза", "Pause", "Приостановить", "Остановить")
_RESUME_BUTTON_NAMES = ("Запустить", "Resume", "Возобновить", "Включить")
_CONFIRM_BUTTON_NAMES = ("Подтвердить", "Confirm", "ОК", "OK")


@dataclass(slots=True, frozen=True)
class _FoundRow:
    """Найденная строка объявления на странице Ads Manager."""

    row: Any
    page: Any


class FacebookAdsActionExecutor:
    """Выполняет отдельные действия в Ads Manager через Playwright."""

    def __init__(self, session_manager: BrowserSessionManager) -> None:
        self._session_manager = session_manager

    async def pause_ad(
        self,
        profile_id: str,
        browser_host_name: str,
        fb_ad_id: str,
    ) -> BrowserActionResult:
        return await self._execute_action(
            profile_id=profile_id,
            browser_host_name=browser_host_name,
            fb_ad_id=fb_ad_id,
            button_names=_PAUSE_BUTTON_NAMES,
            action_name="паузы",
            success_message="переведено на паузу",
            action_log_label="на паузу",
            error_action_label="паузы",
        )

    async def resume_ad(
        self,
        profile_id: str,
        browser_host_name: str,
        fb_ad_id: str,
    ) -> BrowserActionResult:
        return await self._execute_action(
            profile_id=profile_id,
            browser_host_name=browser_host_name,
            fb_ad_id=fb_ad_id,
            button_names=_RESUME_BUTTON_NAMES,
            action_name="возобновления",
            success_message="возобновлено",
            action_log_label="в ротацию",
            error_action_label="возобновления",
        )

    async def _execute_action(
        self,
        *,
        profile_id: str,
        browser_host_name: str,
        fb_ad_id: str,
        button_names: tuple[str, ...],
        action_name: str,
        success_message: str,
        action_log_label: str,
        error_action_label: str,
    ) -> BrowserActionResult:
        logger = logging.getLogger(__name__)
        attached_session: AttachedBrowserSession | None = None

        try:
            attached_session = await self._session_manager.ensure_session(profile_id)
            found_row = await self._find_ad_row(attached_session, fb_ad_id)
            if found_row is None:
                return BrowserActionResult(
                    success=False,
                    message=f"Не удалось найти объявление {fb_ad_id} для {action_name}",
                    fb_ad_id=fb_ad_id,
                    profile_id=profile_id,
                    browser_host_name=browser_host_name,
                )

            await self._select_ad_row(found_row.row)

            action_performed = await self._click_first_available_button(
                found_row.page,
                button_names,
            )
            if not action_performed:
                return BrowserActionResult(
                    success=False,
                    message=f"Не удалось найти кнопку {action_name} для объявления {fb_ad_id}",
                    fb_ad_id=fb_ad_id,
                    profile_id=profile_id,
                    browser_host_name=browser_host_name,
                )

            await self._click_first_available_button(
                found_row.page,
                _CONFIRM_BUTTON_NAMES,
                required=False,
            )

            logger.info(
                "Объявление %s профиля %s переведено %s",
                fb_ad_id,
                profile_id,
                action_log_label,
            )
            return BrowserActionResult(
                success=True,
                message=f"Объявление {fb_ad_id} {success_message}",
                fb_ad_id=fb_ad_id,
                profile_id=profile_id,
                browser_host_name=browser_host_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Не удалось выполнить действие %s для объявления %s профиля %s: %s",
                error_action_label,
                fb_ad_id,
                profile_id,
                exc,
            )
            return BrowserActionResult(
                success=False,
                message=f"Не удалось выполнить действие {error_action_label} для объявления {fb_ad_id}: {exc}",
                fb_ad_id=fb_ad_id,
                profile_id=profile_id,
                browser_host_name=browser_host_name,
            )
        finally:
            if attached_session is not None:
                with contextlib.suppress(Exception):
                    await self._session_manager.release_session(attached_session)

    async def _find_ad_row(
        self,
        session: AttachedBrowserSession,
        fb_ad_id: str,
    ) -> _FoundRow | None:
        for page in self._iter_pages(session):
            row = await self._find_row_on_page(page, fb_ad_id)
            if row is not None:
                return _FoundRow(row=row, page=page)
        return None

    def _iter_pages(self, session: AttachedBrowserSession) -> Iterable[Any]:
        seen_pages: list[Any] = []
        context = session.context
        if context is not None:
            seen_pages.extend(self._get_pages_from_context(context))

        browser = session.browser
        if browser is not None:
            for browser_context in getattr(browser, "contexts", []) or []:
                seen_pages.extend(self._get_pages_from_context(browser_context))

        return seen_pages

    @staticmethod
    def _get_pages_from_context(context: Any) -> list[Any]:
        pages = getattr(context, "pages", None)
        if pages is None:
            return []
        if callable(pages):
            with contextlib.suppress(Exception):
                return list(pages())
            return []
        return list(pages)

    async def _find_row_on_page(self, page: Any, fb_ad_id: str) -> Any | None:
        row_locator = self._get_row_locator(page)
        if row_locator is None:
            return None

        count = await self._locator_count(row_locator)
        for index in range(count):
            row = self._locator_nth(row_locator, index)
            text = await self._read_locator_text(row)
            if fb_ad_id in text:
                return row
        return None

    @staticmethod
    def _get_row_locator(page: Any) -> Any | None:
        locator = getattr(page, "locator", None)
        if locator is None:
            return None
        return locator("[role='row']")

    async def _select_ad_row(self, row: Any) -> None:
        click = getattr(row, "click", None)
        if click is None:
            return
        await click()

    async def _click_first_available_button(
        self,
        page: Any,
        button_names: tuple[str, ...],
        *,
        required: bool = True,
    ) -> bool:
        for button_name in button_names:
            button = self._get_button_locator(page, button_name)
            if button is None:
                continue

            try:
                click = getattr(button, "click", None)
                if click is None:
                    continue
                await click()
                return True
            except Exception:  # noqa: BLE001
                continue

        if required:
            return False
        return True

    @staticmethod
    def _get_button_locator(page: Any, button_name: str) -> Any | None:
        get_by_role = getattr(page, "get_by_role", None)
        if get_by_role is not None:
            with contextlib.suppress(Exception):
                return get_by_role("button", name=button_name)

        locator = getattr(page, "locator", None)
        if locator is None:
            return None
        with contextlib.suppress(Exception):
            return locator(f"button:has-text('{button_name}')")
        return None

    @staticmethod
    async def _locator_count(locator: Any) -> int:
        count = getattr(locator, "count", None)
        if count is None:
            return 0
        return int(await count())

    @staticmethod
    def _locator_nth(locator: Any, index: int) -> Any:
        nth = getattr(locator, "nth", None)
        if nth is None:
            return locator
        return nth(index)

    async def _read_locator_text(self, locator: Any) -> str:
        for method_name in ("inner_text", "text_content"):
            method = getattr(locator, method_name, None)
            if method is None:
                continue
            with contextlib.suppress(Exception):
                value = await method()
                if value:
                    return str(value)

        all_inner_texts = getattr(locator, "all_inner_texts", None)
        if all_inner_texts is not None:
            with contextlib.suppress(Exception):
                values = await all_inner_texts()
                if values:
                    return " ".join(str(item) for item in values)
        return ""
