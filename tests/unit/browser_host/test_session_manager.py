from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.browser_host.adapters.models import (
    AdapterHealth,
    AutomationLaunchResult,
    ProfileStatus,
)
from apps.browser_host.playwright_attach import AttachedBrowserSession
from apps.browser_host.session_manager import BrowserSessionManager


class _FakeAdapter:
    """Заглушка адаптера anti-detect браузера для тестирования менеджера сессий."""

    def __init__(
        self,
        *,
        health: AdapterHealth | None = None,
        profile_status: ProfileStatus | None = None,
        launch_result: AutomationLaunchResult | None = None,
    ) -> None:
        self._health = health or AdapterHealth(is_healthy=True, message="OK")
        self._profile_status = profile_status or ProfileStatus(
            profile_id="test-profile",
            state="stopped",
            has_automation_binding=False,
        )
        self._launch_result = launch_result or AutomationLaunchResult(
            profile_id="test-profile",
            vendor="vision",
            cdp_url="http://localhost:9222",
            webdriver_url=None,
            debug_port=9222,
            browser_pid=12345,
            launched_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        self.ensure_single_called = False
        self.stop_called = False
        self.start_called_with: tuple[str, str, list[str]] | None = None

    async def healthcheck(self) -> AdapterHealth:
        return self._health

    async def ensure_single_active_profile(self) -> None:
        self.ensure_single_called = True

    async def get_profile_status(self, profile_id: str) -> ProfileStatus:
        return self._profile_status

    async def stop_profile(self, profile_id: str) -> None:
        self.stop_called = True

    async def start_profile_for_automation(
        self,
        profile_id: str,
        launch_mode: str,
        launch_args: list[str] | None = None,
    ) -> AutomationLaunchResult:
        self.start_called_with = (profile_id, launch_mode, launch_args or [])
        return self._launch_result

    async def list_profiles(self):
        return []

    async def list_open_profiles(self):
        return []


class _FakePlaywrightAttachService:
    """Заглушка Playwright для тестирования менеджера сессий."""

    def __init__(self, *, session: AttachedBrowserSession | None = None) -> None:
        self._session = session or AttachedBrowserSession(
            profile_id="test-profile",
            cdp_url="http://localhost:9222",
            webdriver_url=None,
            is_attached=True,
            attached_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        self.detach_called = False

    async def attach(self, launch_result: AutomationLaunchResult) -> AttachedBrowserSession:
        return self._session

    async def detach(self, session: AttachedBrowserSession) -> None:
        self.detach_called = True


# Проверяет, что healthcheck делегируется адаптеру.
@pytest.mark.asyncio
async def test_healthcheck_delegates_to_adapter() -> None:
    health = AdapterHealth(is_healthy=True, message="Всё в порядке")
    adapter = _FakeAdapter(health=health)
    manager = BrowserSessionManager(
        adapter=adapter,
        playwright_attach_service=_FakePlaywrightAttachService(),
    )
    result = await manager.healthcheck()
    assert result.is_healthy is True
    assert result.message == "Всё в порядке"


# Проверяет, что менеджер останавливает профиль без automation binding перед запуском.
@pytest.mark.asyncio
async def test_ensure_session_stops_profile_without_automation_binding() -> None:
    adapter = _FakeAdapter(
        profile_status=ProfileStatus(
            profile_id="p-1",
            state="running",
            has_automation_binding=False,
        ),
    )
    attach_service = _FakePlaywrightAttachService()
    manager = BrowserSessionManager(adapter=adapter, playwright_attach_service=attach_service)

    session = await manager.ensure_session("p-1")

    assert adapter.ensure_single_called is True
    assert adapter.stop_called is True
    assert adapter.start_called_with is not None
    assert adapter.start_called_with[0] == "p-1"
    assert session.is_attached is True


# Проверяет, что менеджер НЕ останавливает профиль с уже готовым automation binding.
@pytest.mark.asyncio
async def test_ensure_session_skips_stop_when_automation_binding_exists() -> None:
    adapter = _FakeAdapter(
        profile_status=ProfileStatus(
            profile_id="p-1",
            state="running",
            has_automation_binding=True,
        ),
    )
    attach_service = _FakePlaywrightAttachService()
    manager = BrowserSessionManager(adapter=adapter, playwright_attach_service=attach_service)

    session = await manager.ensure_session("p-1")

    assert adapter.ensure_single_called is True
    assert adapter.stop_called is False
    assert session.is_attached is True


# Проверяет, что release_session вызывает detach у Playwright.
@pytest.mark.asyncio
async def test_release_session_calls_detach() -> None:
    attach_service = _FakePlaywrightAttachService()
    manager = BrowserSessionManager(
        adapter=_FakeAdapter(),
        playwright_attach_service=attach_service,
    )
    session = AttachedBrowserSession(
        profile_id="p-1",
        cdp_url="http://localhost:9222",
        webdriver_url=None,
        is_attached=True,
    )
    await manager.release_session(session)
    assert attach_service.detach_called is True


# Проверяет, что ensure_session передает launch_args адаптеру.
@pytest.mark.asyncio
async def test_ensure_session_passes_launch_args() -> None:
    adapter = _FakeAdapter()
    attach_service = _FakePlaywrightAttachService()
    manager = BrowserSessionManager(adapter=adapter, playwright_attach_service=attach_service)

    await manager.ensure_session("p-1", launch_args=["--disable-gpu"])

    assert adapter.start_called_with is not None
    assert adapter.start_called_with[2] == ["--disable-gpu"]
