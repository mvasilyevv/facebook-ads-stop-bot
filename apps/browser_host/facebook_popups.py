from __future__ import annotations

import contextlib
from typing import Any

_KNOWN_ADS_MANAGER_POPUP_MARKERS = (
    "выключите блокирование рекламы",
    "рекламные инструменты meta могут работать не так, как ожидается",
    "в браузере включена блокировка рекламы",
    "turn off ad blocking",
    "meta ad tools may not work as expected",
    "ad blocking is enabled in your browser",
)
_KNOWN_ADS_MANAGER_POPUP_BUTTONS = ("ОК", "Ок", "OK", "Ok", "Закрыть", "Close")
_KNOWN_ADS_MANAGER_POPUP_DISMISS_ATTEMPTS = 3
_KNOWN_ADS_MANAGER_POPUP_WAIT_MS = 200


async def dismiss_known_ads_manager_popups(page: Any) -> bool:
    """Закрывает известные блокирующие окна Ads Manager, если они появились."""

    if not await _is_known_popup_visible(page):
        return False

    for _ in range(_KNOWN_ADS_MANAGER_POPUP_DISMISS_ATTEMPTS):
        if not await _dismiss_popup_once(page):
            return False
        if await _wait_until_popup_closed(page):
            return True

    return not await _is_known_popup_visible(page)


def _contains_known_popup_marker(text: str) -> bool:
    normalized_text = text.casefold()
    return any(marker in normalized_text for marker in _KNOWN_ADS_MANAGER_POPUP_MARKERS)


async def _read_page_text(page: Any) -> str:
    body_text = getattr(page, "body_text", None)
    if isinstance(body_text, str):
        return body_text

    evaluate = getattr(page, "evaluate", None)
    if evaluate is None:
        return ""

    with contextlib.suppress(Exception):
        value = await evaluate(
            "() => document.body ? (document.body.innerText || '') : ''",
            None,
        )
        if isinstance(value, str):
            return value

    return ""


async def _is_known_popup_visible(page: Any) -> bool:
    dialog = await _get_known_popup_dialog(page)
    if dialog is not None:
        return await _locator_is_visible(dialog)

    popup_text = await _read_page_text(page)
    return _contains_known_popup_marker(popup_text)


async def _dismiss_popup_once(page: Any) -> bool:
    dialog = await _get_known_popup_dialog(page)
    if dialog is not None:
        return await _click_first_available_button(dialog, _KNOWN_ADS_MANAGER_POPUP_BUTTONS)

    for container in (page,):
        if await _click_first_available_button(container, _KNOWN_ADS_MANAGER_POPUP_BUTTONS):
            return True
    return False


async def _wait_until_popup_closed(page: Any) -> bool:
    for _ in range(3):
        if not await _is_known_popup_visible(page):
            return True
        await _wait_after_popup_click(page)
    return not await _is_known_popup_visible(page)


async def _get_known_popup_dialog(page: Any) -> Any | None:
    for candidate in await _iter_known_popup_dialog_candidates(page):
        if not await _locator_is_visible(candidate):
            continue
        if await _locator_contains_known_popup_marker(candidate):
            return candidate
    return None


async def _click_first_available_button(container: Any, button_names: tuple[str, ...]) -> bool:
    for button_name in button_names:
        for locator in await _iter_button_locators(container, button_name):
            for candidate in await _expand_locator_candidates(locator):
                if not await _locator_is_visible(candidate):
                    continue
                click = getattr(candidate, "click", None)
                if click is None:
                    continue
                with contextlib.suppress(Exception):
                    await click()
                    return True
    return False


async def _iter_button_locators(container: Any, button_name: str) -> tuple[Any, ...]:
    locators: list[Any] = []

    get_by_role = getattr(container, "get_by_role", None)
    if get_by_role is not None:
        with contextlib.suppress(Exception):
            locator = get_by_role("button", name=button_name)
            if locator is not None:
                locators.append(locator)

    locator_factory = getattr(container, "locator", None)
    if locator_factory is None:
        return tuple(locators)

    for selector in (
        f"button:has-text('{button_name}')",
        f"[role='button']:has-text('{button_name}')",
        f"text={button_name}",
    ):
        with contextlib.suppress(Exception):
            candidate = locator_factory(selector)
            if candidate is not None:
                locators.append(candidate)
    return tuple(locators)


async def _expand_locator_candidates(locator: Any) -> tuple[Any, ...]:
    count = getattr(locator, "count", None)
    nth = getattr(locator, "nth", None)
    if callable(count) and callable(nth):
        with contextlib.suppress(Exception):
            total = await count()
            if total <= 0:
                return ()
            if total == 1:
                return (locator,)
            return tuple(nth(index) for index in range(total))
    return (locator,)


async def _locator_is_visible(locator: Any) -> bool:
    is_visible = getattr(locator, "is_visible", None)
    if callable(is_visible):
        with contextlib.suppress(Exception):
            return bool(await is_visible())

    count = getattr(locator, "count", None)
    if callable(count):
        with contextlib.suppress(Exception):
            return (await count()) > 0

    return True


async def _iter_known_popup_dialog_candidates(page: Any) -> tuple[Any, ...]:
    candidates: list[Any] = []

    get_by_role = getattr(page, "get_by_role", None)
    if get_by_role is not None:
        with contextlib.suppress(Exception):
            locator = get_by_role("dialog")
            if locator is not None:
                candidates.extend(await _expand_locator_candidates(locator))

    locator_factory = getattr(page, "locator", None)
    if locator_factory is None:
        return tuple(candidates)

    for selector in ("[role='dialog']", "[aria-modal='true']"):
        with contextlib.suppress(Exception):
            locator = locator_factory(selector)
            if locator is not None:
                candidates.extend(await _expand_locator_candidates(locator))

    return tuple(candidates)


async def _locator_contains_known_popup_marker(locator: Any) -> bool:
    inner_text = getattr(locator, "inner_text", None)
    if callable(inner_text):
        with contextlib.suppress(Exception):
            value = await inner_text()
            if isinstance(value, str) and _contains_known_popup_marker(value):
                return True

    text_content = getattr(locator, "text_content", None)
    if callable(text_content):
        with contextlib.suppress(Exception):
            value = await text_content()
            if isinstance(value, str) and _contains_known_popup_marker(value):
                return True

    return False


async def _wait_after_popup_click(page: Any) -> None:
    wait_for_timeout = getattr(page, "wait_for_timeout", None)
    if wait_for_timeout is None:
        return

    with contextlib.suppress(Exception):
        await wait_for_timeout(_KNOWN_ADS_MANAGER_POPUP_WAIT_MS)
