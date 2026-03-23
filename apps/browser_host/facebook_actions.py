from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from apps.browser_host.facebook_popups import dismiss_known_ads_manager_popups
from apps.browser_host.facebook_service_page import (
    ACTIONS_SERVICE_PAGE,
    ensure_ads_manager_service_page,
    is_ads_manager_service_url,
)
from apps.browser_host.playwright_attach import AttachedBrowserSession
from apps.browser_host.session_manager import BrowserSessionManager
from core.actions import BrowserActionResult

_ADS_ROW_SELECTOR = "div[role='presentation']._1gd4"
_MORE_BUTTON_NAMES = ("Больше", "Ещё", "Еще", "More")
_PAUSE_BUTTON_NAMES = ("Пауза", "Pause", "Приостановить", "Остановить")
_PAUSE_FALLBACK_NAMES = (
    "Выключить",
    "Отключить",
    "Выкл./вкл.",
    "Выкл./вкл. рекламу",
)
_RESUME_BUTTON_NAMES = ("Запустить", "Resume", "Возобновить", "Включить")
_RESUME_FALLBACK_NAMES = ("Выкл./вкл.", "Выкл./вкл. рекламу")
_CONFIRM_BUTTON_NAMES = ("Подтвердить", "Confirm", "ОК", "OK")
_SWITCH_WAIT_ATTEMPTS = 6
_SWITCH_WAIT_TIMEOUT_MS = 250
_ROW_SEARCH_SCROLL_ATTEMPTS = 12
_ROW_SEARCH_SCROLL_STEP_PX = 2200
_ROW_SEARCH_SCROLL_PAUSE_MS = 250


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
            desired_switch_state=False,
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
            desired_switch_state=True,
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
        desired_switch_state: bool,
    ) -> BrowserActionResult:
        logger = logging.getLogger(__name__)
        attached_session: AttachedBrowserSession | None = None

        try:
            attached_session = await self._session_manager.ensure_session(profile_id)
            current_page = await self._resolve_action_page(attached_session, fb_ad_id)
            if current_page is None:
                raise RuntimeError("Не удалось подготовить служебную страницу Ads Manager")
            if await dismiss_known_ads_manager_popups(current_page):
                logger.info(
                    "Закрываю блокирующее окно Ads Manager перед изменением статуса объявления %s",
                    fb_ad_id,
                )

            current_page_action = await self._execute_current_page_flow(
                page=current_page,
                fb_ad_id=fb_ad_id,
                button_names=button_names,
                action_name=action_name,
                desired_switch_state=desired_switch_state,
            )
            if current_page_action:
                await self._confirm_action(current_page)
                logger.info(
                    "Объявление %s профиля %s переведено %s в текущей вкладке Ads Manager",
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

            found_row = await self._find_row_on_page_with_scroll(current_page, fb_ad_id)
            if found_row is None:
                return BrowserActionResult(
                    success=False,
                    message=f"Не удалось найти объявление {fb_ad_id} для {action_name}",
                    fb_ad_id=fb_ad_id,
                    profile_id=profile_id,
                    browser_host_name=browser_host_name,
                )

            fallback_action = await self._execute_row_fallback_flow(
                row=found_row,
                page=current_page,
                fb_ad_id=fb_ad_id,
                button_names=button_names,
                action_name=action_name,
                desired_switch_state=desired_switch_state,
            )
            if not fallback_action:
                return BrowserActionResult(
                    success=False,
                    message=f"Не удалось найти кнопку {action_name} для объявления {fb_ad_id}",
                    fb_ad_id=fb_ad_id,
                    profile_id=profile_id,
                    browser_host_name=browser_host_name,
                )

            await self._confirm_action(current_page)

            logger.info(
                "Объявление %s профиля %s переведено %s через fallback-строку",
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

    async def _resolve_action_page(
        self,
        session: AttachedBrowserSession,
        fb_ad_id: str,
    ) -> Any | None:
        seed_page = self._find_seed_ads_manager_page(session)
        seed_url = getattr(seed_page, "url", "") or ""
        return await ensure_ads_manager_service_page(
            browser=session.browser,
            context=session.context,
            service_role=ACTIONS_SERVICE_PAGE,
            seed_url=seed_url,
            selected_ad_id=fb_ad_id,
        )

    def _find_seed_ads_manager_page(self, session: AttachedBrowserSession) -> Any | None:
        for page in self._iter_pages(session):
            url = getattr(page, "url", "") or ""
            if "adsmanager.facebook.com" in url and not is_ads_manager_service_url(url):
                return page
        for page in self._iter_pages(session):
            url = getattr(page, "url", "") or ""
            if "adsmanager.facebook.com" in url:
                return page
        return None

    async def _execute_current_page_flow(
        self,
        *,
        page: Any,
        fb_ad_id: str,
        button_names: tuple[str, ...],
        action_name: str,
        desired_switch_state: bool,
    ) -> bool:
        if await self._set_switch_state_on_page(
            page,
            fb_ad_id,
            desired_switch_state,
            allow_generic=False,
        ):
            return True

        if not self._page_targets_selected_ad(page, fb_ad_id):
            return False

        return await self._execute_selected_ad_flow(
            page=page,
            fb_ad_id=fb_ad_id,
            button_names=button_names,
            action_name=action_name,
            desired_switch_state=desired_switch_state,
        )

    @staticmethod
    def _page_targets_selected_ad(page: Any, fb_ad_id: str) -> bool:
        url = getattr(page, "url", "") or ""
        selected_values = parse_qs(urlparse(url).query).get("selected_ad_ids", [])
        for value in selected_values:
            for selected_id in str(value).split(","):
                if selected_id.strip() == fb_ad_id:
                    return True
        return False

    async def _execute_selected_ad_flow(
        self,
        *,
        page: Any,
        fb_ad_id: str,
        button_names: tuple[str, ...],
        action_name: str,
        desired_switch_state: bool,
    ) -> bool:
        await dismiss_known_ads_manager_popups(page)

        if await self._set_switch_state_on_page(
            page,
            fb_ad_id,
            desired_switch_state,
        ):
            return True

        if await self._click_action_candidates(page, button_names):
            return True

        more_button_names = _MORE_BUTTON_NAMES
        if await self._click_first_available_button(page, more_button_names, required=False):
            wait_for_timeout = getattr(page, "wait_for_timeout", None)
            if wait_for_timeout is not None:
                await wait_for_timeout(300)
            if await self._click_action_candidates(
                page,
                button_names + self._action_fallback_names(action_name),
            ):
                return True

        return False

    async def _execute_row_fallback_flow(
        self,
        *,
        row: Any,
        page: Any,
        fb_ad_id: str,
        button_names: tuple[str, ...],
        action_name: str,
        desired_switch_state: bool,
    ) -> bool:
        await dismiss_known_ads_manager_popups(page)

        if await self._set_switch_state_on_row(row, page, desired_switch_state):
            return True

        await self._select_ad_row(row)
        await dismiss_known_ads_manager_popups(page)
        if await self._click_action_candidates(
            page,
            button_names + self._action_fallback_names(action_name),
        ):
            return True

        return await self._set_switch_state_on_page(
            page,
            fb_ad_id,
            desired_switch_state,
            allow_generic=False,
        )

    async def _confirm_action(self, page: Any) -> None:
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if wait_for_timeout is not None:
            await wait_for_timeout(500)

        await dismiss_known_ads_manager_popups(page)
        await self._click_first_available_button(
            page,
            _CONFIRM_BUTTON_NAMES,
            required=False,
        )
        await dismiss_known_ads_manager_popups(page)

    @staticmethod
    def _action_fallback_names(action_name: str) -> tuple[str, ...]:
        if action_name == "паузы":
            return _PAUSE_FALLBACK_NAMES
        if action_name == "возобновления":
            return _RESUME_FALLBACK_NAMES
        return ()

    async def _find_row_on_page_with_scroll(
        self,
        page: Any,
        fb_ad_id: str,
    ) -> Any | None:
        await dismiss_known_ads_manager_popups(page)
        row = await self._find_row_on_page(page, fb_ad_id)
        if row is not None:
            return row

        row = await self._search_row_while_scrolling(page, fb_ad_id, direction="down")
        if row is not None:
            return row

        await self._scroll_search_area_to_edge(page, edge="top")

        row = await self._find_row_on_page(page, fb_ad_id)
        if row is not None:
            return row

        return await self._search_row_while_scrolling(page, fb_ad_id, direction="down")

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
            if await self._row_matches_ad_id(row, fb_ad_id):
                return row
        return None

    async def _search_row_while_scrolling(
        self,
        page: Any,
        fb_ad_id: str,
        *,
        direction: str,
    ) -> Any | None:
        for _ in range(_ROW_SEARCH_SCROLL_ATTEMPTS):
            await dismiss_known_ads_manager_popups(page)
            scrolled = await self._scroll_search_area(page, direction=direction)
            if not scrolled:
                return None
            await dismiss_known_ads_manager_popups(page)
            row = await self._find_row_on_page(page, fb_ad_id)
            if row is not None:
                return row
        return None

    @staticmethod
    def _get_row_locator(page: Any) -> Any | None:
        locator = getattr(page, "locator", None)
        if locator is None:
            return None
        return locator(_ADS_ROW_SELECTOR)

    async def _row_matches_ad_id(self, row: Any, fb_ad_id: str) -> bool:
        text = await self._read_locator_text(row)
        if fb_ad_id in text:
            return True

        evaluate = getattr(row, "evaluate", None)
        if evaluate is None:
            return False

        with contextlib.suppress(Exception):
            return bool(
                await evaluate(
                    """(element, adId) => Array.from(element.querySelectorAll('[data-surface]')).some(
                        (node) => (node.getAttribute('data-surface') || '').includes(`table_row:${adId}`)
                    )""",
                    fb_ad_id,
                )
            )

        return False

    async def _select_ad_row(self, row: Any) -> None:
        click = getattr(row, "click", None)
        if click is None:
            return
        await click()

    async def _set_switch_state_on_page(
        self,
        page: Any,
        fb_ad_id: str,
        desired_switch_state: bool,
        *,
        allow_generic: bool = True,
    ) -> bool:
        for selector in self._build_switch_selectors(fb_ad_id, allow_generic=allow_generic):
            switch = await self._find_first_locator(page, selector)
            if switch is None:
                continue
            if await self._set_switch_state(switch, page, desired_switch_state):
                return True
        return False

    async def _set_switch_state_on_row(
        self,
        row: Any,
        page: Any,
        desired_switch_state: bool,
    ) -> bool:
        for selector in ("input[role='switch']", "[role='switch']"):
            switch = await self._find_first_locator(row, selector)
            if switch is None:
                continue
            if await self._set_switch_state(switch, page, desired_switch_state):
                return True
        return False

    async def _set_switch_state(
        self,
        switch: Any,
        page: Any,
        desired_switch_state: bool,
    ) -> bool:
        current_state = await self._read_switch_state(switch)
        if current_state is not None and current_state == desired_switch_state:
            return True

        click = getattr(switch, "click", None)
        if click is None:
            return False

        with contextlib.suppress(Exception):
            await click()
            state_reached = await self._wait_for_switch_state(
                switch,
                page,
                desired_switch_state,
            )
            return state_reached is not False

        return False

    @staticmethod
    def _build_switch_selectors(
        fb_ad_id: str,
        *,
        allow_generic: bool,
    ) -> tuple[str, ...]:
        selectors = [
            f"[data-surface*='table_row:{fb_ad_id}unit/table_cell:forObjectType(toggle,ADGROUP)'] input[role='switch']",
            f"[data-surface*='table_row:{fb_ad_id}unit/table_cell:forObjectType(toggle,ADGROUP)'] [role='switch']",
            f"[data-surface*='table_row:{fb_ad_id}unit'] input[role='switch']",
            f"[data-surface*='table_row:{fb_ad_id}unit'] [role='switch']",
        ]
        if allow_generic:
            selectors.extend(("input[role='switch']", "[role='switch']"))
        return tuple(selectors)

    async def _find_first_locator(
        self,
        container: Any,
        selector: str,
    ) -> Any | None:
        locator = getattr(container, "locator", None)
        if locator is None:
            return None

        with contextlib.suppress(Exception):
            candidate = locator(selector)
            if candidate is None:
                return None

            count = getattr(candidate, "count", None)
            if count is None:
                return candidate

            if int(await count()) <= 0:
                return None
            return self._locator_nth(candidate, 0)

        return None

    async def _wait_for_switch_state(
        self,
        switch: Any,
        page: Any,
        desired_switch_state: bool,
    ) -> bool | None:
        for _ in range(_SWITCH_WAIT_ATTEMPTS):
            current_state = await self._read_switch_state(switch)
            if current_state is None:
                return None
            if current_state == desired_switch_state:
                return True

            wait_for_timeout = getattr(page, "wait_for_timeout", None)
            if wait_for_timeout is not None:
                await wait_for_timeout(_SWITCH_WAIT_TIMEOUT_MS)

        return False

    async def _read_switch_state(self, switch: Any) -> bool | None:
        get_attribute = getattr(switch, "get_attribute", None)
        if get_attribute is not None:
            with contextlib.suppress(Exception):
                state = self._normalize_switch_state(await get_attribute("aria-checked"))
                if state is not None:
                    return state

        is_checked = getattr(switch, "is_checked", None)
        if is_checked is not None:
            with contextlib.suppress(Exception):
                return bool(await is_checked())

        return None

    @staticmethod
    def _normalize_switch_state(value: Any) -> bool | None:
        if value is None:
            return None

        normalized = str(value).strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        return None

    async def _click_action_candidates(
        self,
        page: Any,
        action_names: tuple[str, ...],
    ) -> bool:
        for action_name in action_names:
            locator = self._get_action_locator(page, action_name)
            if locator is None:
                continue

            try:
                click = getattr(locator, "click", None)
                if click is None:
                    continue
                await click()
                return True
            except Exception:  # noqa: BLE001
                continue

        return False

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
    def _get_action_locator(page: Any, action_name: str) -> Any | None:
        get_by_role = getattr(page, "get_by_role", None)
        if get_by_role is not None:
            for role in ("button", "menuitem", "tab"):
                with contextlib.suppress(Exception):
                    locator = get_by_role(role, name=action_name)
                    if locator is not None:
                        return locator

        locator = getattr(page, "locator", None)
        if locator is None:
            return None
        for selector in (
            f"button:has-text('{action_name}')",
            f"[role='menuitem']:has-text('{action_name}')",
            f"text={action_name}",
        ):
            with contextlib.suppress(Exception):
                candidate = locator(selector)
                if candidate is not None:
                    return candidate
        return None

    @staticmethod
    def _get_button_locator(page: Any, button_name: str) -> Any | None:
        return FacebookAdsActionExecutor._get_action_locator(page, button_name)

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

    async def _scroll_search_area(self, page: Any, *, direction: str) -> bool:
        evaluate = getattr(page, "evaluate", None)
        if evaluate is None:
            return False

        try:
            script = """
                ([direction, step]) => {
                    let containers = [
                        ...document.querySelectorAll('.uiScrollableAreaWrap, .uiScrollableAreaBody, div[role="grid"], div[role="table"]'),
                        document.scrollingElement,
                        document.body,
                        window
                    ];
                    let delta = direction === 'down' ? step : -step;
                    for (let c of containers) {
                        if (c && c.scrollBy) {
                            c.scrollBy({top: delta, behavior: 'auto'});
                        } else if (c && typeof c.scrollTop !== 'undefined') {
                            c.scrollTop += delta;
                        }
                    }
                }
            """
            await evaluate(script, [direction, _ROW_SEARCH_SCROLL_STEP_PX])

            wait_for_timeout = getattr(page, "wait_for_timeout", None)
            if wait_for_timeout is not None:
                await wait_for_timeout(_ROW_SEARCH_SCROLL_PAUSE_MS)

            return True
        except Exception:
            return False

    async def _scroll_search_area_to_edge(self, page: Any, *, edge: str) -> None:
        evaluate = getattr(page, "evaluate", None)
        if evaluate is None:
            return

        try:
            script = """
                (edge) => {
                    let containers = [
                        ...document.querySelectorAll('.uiScrollableAreaWrap, .uiScrollableAreaBody, div[role="grid"], div[role="table"]'),
                        document.scrollingElement,
                        document.body,
                        window
                    ];
                    for (let c of containers) {
                        let y = edge === 'top' ? 0 : (c.scrollHeight || 999999);
                        if (c && c.scrollTo) {
                            c.scrollTo({top: y, behavior: 'auto'});
                        } else if (c && typeof c.scrollTop !== 'undefined') {
                            c.scrollTop = y;
                        }
                    }
                }
            """
            await evaluate(script, edge)

            wait_for_timeout = getattr(page, "wait_for_timeout", None)
            if wait_for_timeout is not None:
                await wait_for_timeout(_ROW_SEARCH_SCROLL_PAUSE_MS * 2)
        except Exception:
            pass
