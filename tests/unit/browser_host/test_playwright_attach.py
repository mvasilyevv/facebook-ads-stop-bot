from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from apps.browser_host import playwright_attach as playwright_attach_module
from apps.browser_host.adapters.models import AutomationLaunchResult
from apps.browser_host.playwright_attach import PlaywrightAttachService


class _FakeBrowser:
    def __init__(self) -> None:
        self.contexts = [SimpleNamespace(name="context-1")]


class _FakePlaywright:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = _FakeChromium(browser)
        self.stop_called = False

    async def stop(self) -> None:
        self.stop_called = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser
        self.connected_urls: list[str] = []

    async def connect_over_cdp(self, cdp_url: str) -> _FakeBrowser:
        self.connected_urls.append(cdp_url)
        return self._browser


class _FakePlaywrightManager:
    def __init__(self, playwright: _FakePlaywright) -> None:
        self._playwright = playwright
        self.start_called = False

    async def start(self) -> _FakePlaywright:
        self.start_called = True
        return self._playwright


def _build_launch_result(cdp_url: str | None) -> AutomationLaunchResult:
    return AutomationLaunchResult(
        profile_id="profile-1",
        vendor="vision",
        cdp_url=cdp_url,
        webdriver_url=None,
        debug_port=54000,
        browser_pid=4321,
        launched_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
    )


# Проверяет, что Playwright attach реально подключается к CDP и возвращает живую сессию.
@pytest.mark.asyncio
async def test_playwright_attach_connects_over_cdp() -> None:
    browser = _FakeBrowser()
    playwright = _FakePlaywright(browser)
    manager = _FakePlaywrightManager(playwright)
    service = PlaywrightAttachService(playwright_factory=lambda: manager)

    session = await service.attach(_build_launch_result("http://127.0.0.1:54000"))

    assert manager.start_called is True
    assert playwright.chromium.connected_urls == ["http://127.0.0.1:54000"]
    assert session.profile_id == "profile-1"
    assert session.is_attached is True
    assert session.cdp_url == "http://127.0.0.1:54000"
    assert session.webdriver_url is None
    assert session.browser is browser
    assert session.context is browser.contexts[0]
    assert session.context_count == 1
    assert session.playwright is playwright
    assert session.attached_at is not None
    assert session.attached_at.tzinfo == UTC


# Проверяет, что сервис возвращает понятную русскую ошибку, если Playwright не установлен.
@pytest.mark.asyncio
async def test_playwright_attach_reports_missing_playwright_package(monkeypatch) -> None:
    def _raise_missing_package() -> None:
        raise RuntimeError("Для подключения через Playwright нужно установить пакет `playwright`.")

    monkeypatch.setattr(
        playwright_attach_module, "_load_async_playwright_factory", lambda: _raise_missing_package
    )  # noqa: E501
    service = PlaywrightAttachService()

    with pytest.raises(RuntimeError, match="установить пакет `playwright`"):
        await service.attach(_build_launch_result("http://127.0.0.1:54000"))


# Проверяет, что сервис возвращает понятную русскую ошибку, если CDP endpoint недоступен.
@pytest.mark.asyncio
async def test_playwright_attach_reports_unavailable_cdp_endpoint() -> None:
    class _BrokenChromium(_FakeChromium):
        async def connect_over_cdp(self, cdp_url: str) -> _FakeBrowser:
            raise ConnectionError("соединение отклонено")

    class _BrokenPlaywright(_FakePlaywright):
        def __init__(self, browser: _FakeBrowser) -> None:
            self.chromium = _BrokenChromium(browser)
            self.stop_called = False

    browser = _FakeBrowser()
    playwright = _BrokenPlaywright(browser)
    manager = _FakePlaywrightManager(playwright)
    service = PlaywrightAttachService(playwright_factory=lambda: manager)

    with pytest.raises(RuntimeError, match="Не удалось подключиться к CDP"):
        await service.attach(_build_launch_result("http://127.0.0.1:54000"))

    assert playwright.stop_called is True
