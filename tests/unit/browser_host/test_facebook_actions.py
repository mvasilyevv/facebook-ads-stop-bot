from __future__ import annotations

from dataclasses import dataclass

import pytest

from apps.browser_host.facebook_actions import FacebookAdsActionExecutor
from apps.browser_host.playwright_attach import AttachedBrowserSession


@dataclass(slots=True)
class _FakeRow:
    """Заглушка строки таблицы Ads Manager."""

    text: str
    clicked: bool = False

    async def click(self) -> None:
        self.clicked = True

    async def inner_text(self) -> str:
        return self.text


class _FakeRowCollection:
    """Заглушка коллекции строк таблицы Ads Manager."""

    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    async def count(self) -> int:
        return len(self._rows)

    def nth(self, index: int) -> _FakeRow:
        return self._rows[index]


class _FakeButton:
    """Заглушка кнопки действия в Ads Manager."""

    def __init__(self, name: str, clicked: list[str]) -> None:
        self._name = name
        self._clicked = clicked

    async def click(self) -> None:
        self._clicked.append(self._name)


class _FakePage:
    """Заглушка страницы Playwright для тестирования pause executor."""

    def __init__(self, rows: list[_FakeRow], clicked_buttons: list[str]) -> None:
        self._rows = rows
        self._clicked_buttons = clicked_buttons

    def locator(self, selector: str) -> object:
        if selector == "[role='row']":
            return _FakeRowCollection(self._rows)
        if selector.startswith("button:has-text("):
            name = selector.split("button:has-text('", 1)[1].rsplit("')", 1)[0]
            return _FakeButton(name, self._clicked_buttons)
        raise AssertionError(f"Неожиданный селектор: {selector}")

    def get_by_role(self, role: str, name: str) -> _FakeButton:
        if role != "button":
            raise AssertionError(f"Неожиданная роль: {role}")
        return _FakeButton(name, self._clicked_buttons)


class _FakeContext:
    """Заглушка browser context."""

    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages


class _FakeBrowser:
    """Заглушка browser с одним открытым context."""

    def __init__(self, context: _FakeContext) -> None:
        self.contexts = [context]


class _FakeSessionManager:
    """Заглушка manager для проверки pause executor без Playwright runtime."""

    def __init__(self, session: AttachedBrowserSession) -> None:
        self._session = session
        self.released = False

    async def ensure_session(self, profile_id: str) -> AttachedBrowserSession:
        return self._session

    async def release_session(self, session: AttachedBrowserSession) -> None:
        self.released = True


def _build_executor(
    session: AttachedBrowserSession,
) -> tuple[FacebookAdsActionExecutor, _FakeSessionManager]:
    """Собирает executor с фейковым manager для локального теста."""

    manager = _FakeSessionManager(session)
    executor = FacebookAdsActionExecutor(session_manager=manager)
    return executor, manager


# Проверяет, что executor находит объявление и нажимает паузу на странице Ads Manager.
@pytest.mark.asyncio
async def test_pause_ad_success() -> None:
    rows = [_FakeRow("Объявление 1 1234567890123"), _FakeRow("Другое объявление")]
    clicked_buttons: list[str] = []
    page = _FakePage(rows, clicked_buttons)
    context = _FakeContext([page])
    browser = _FakeBrowser(context)
    attached_session = AttachedBrowserSession(
        profile_id="profile-1",
        cdp_url="http://127.0.0.1:54000",
        webdriver_url=None,
        is_attached=True,
        browser=browser,
        context=context,
    )
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert result.fb_ad_id == "1234567890123"
    assert "переведено на паузу" in result.message
    assert rows[0].clicked is True
    assert "Пауза" in clicked_buttons
    assert manager.released is True


# Проверяет, что executor возвращает понятное сообщение, если объявление не найдено.
@pytest.mark.asyncio
async def test_pause_ad_returns_not_found_message() -> None:
    rows = [_FakeRow("Другое объявление")]
    clicked_buttons: list[str] = []
    page = _FakePage(rows, clicked_buttons)
    context = _FakeContext([page])
    browser = _FakeBrowser(context)
    attached_session = AttachedBrowserSession(
        profile_id="profile-1",
        cdp_url="http://127.0.0.1:54000",
        webdriver_url=None,
        is_attached=True,
        browser=browser,
        context=context,
    )
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is False
    assert result.message == "Не удалось найти объявление 1234567890123 для паузы"
    assert clicked_buttons == []
    assert manager.released is True
