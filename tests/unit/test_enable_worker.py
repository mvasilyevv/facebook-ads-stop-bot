# -*- coding: utf-8 -*-
"""Тесты для enable worker: humanized клик и корректный early return."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


class FakeEnableToggle:
    """Фейковый переключатель объявления."""

    def __init__(self, aria_checked: str) -> None:
        self.aria_checked = aria_checked
        self.click = AsyncMock()

    async def get_attribute(self, name: str) -> str | None:
        if name == "aria-checked":
            return self.aria_checked
        return None


class FakeEnableCell:
    """Фейковая ячейка с переключателем."""

    def __init__(self, toggle: FakeEnableToggle) -> None:
        self.toggle = toggle

    async def query_selector(self, selector: str):
        if selector == '[role="switch"][aria-checked]':
            return self.toggle
        if selector == 'input[role="switch"]':
            return self.toggle
        if selector == 'input[type="checkbox"]':
            return None
        if selector == '[role="switch"]':
            return self.toggle
        return None


class FakeEnablePage:
    """Фейковая страница Ads Manager."""

    def __init__(self, cell: FakeEnableCell | None) -> None:
        self._cell = cell
        self.evaluate = AsyncMock()
        self.screenshot = AsyncMock()

    async def query_selector(self, selector: str):
        if self._cell is not None and (
            "forObjectType(toggle" in selector or "table_row:" in selector
        ):
            return self._cell
        return None

    async def query_selector_all(self, selector: str):
        return []


# Тест: включение переключателя должно идти через human_click без прямого click.
@pytest.mark.asyncio
async def test_execute_enable_uses_human_click_for_toggle():
    """Включение должно кликать по переключателю через humanizer."""
    from run_enable_worker import execute_enable_via_playwright

    toggle = FakeEnableToggle("false")
    page = FakeEnablePage(FakeEnableCell(toggle))

    async def mock_human_click(_page, element, double_check_pause=True):
        element.aria_checked = "true"

    with (
        patch("run_enable_worker.asyncio.sleep", new=AsyncMock()),
        patch(
            "run_enable_worker.human_click", new=AsyncMock(side_effect=mock_human_click)
        ) as human_click,
        patch(
            "run_enable_worker._wait_for_enable_confirmation",
            new=AsyncMock(
                return_value=(
                    True,
                    "Объявление включено: переключатель дважды подтвердил состояние ON",
                )
            ),
        ),
    ):
        success, message = await execute_enable_via_playwright(page, "123456")

    assert success is True
    assert "подтвердил состояние ON" in message
    human_click.assert_awaited_once_with(page, toggle, double_check_pause=True)
    toggle.click.assert_not_awaited()


# Тест: если объявление уже включено, human_click не вызывается.
@pytest.mark.asyncio
async def test_execute_enable_skips_click_when_already_enabled():
    """Если переключатель уже включён, воркер не должен кликать."""
    from run_enable_worker import execute_enable_via_playwright

    toggle = FakeEnableToggle("true")
    page = FakeEnablePage(FakeEnableCell(toggle))

    with patch("run_enable_worker.human_click", new=AsyncMock()) as human_click:
        success, message = await execute_enable_via_playwright(page, "123456")

    assert success is True
    assert "уже включено" in message
    human_click.assert_not_awaited()
    toggle.click.assert_not_awaited()


# Тест: если строка пропала из DOM, worker должен пройти таблицу Ads Manager до нужного объявления.
@pytest.mark.asyncio
async def test_execute_enable_restores_row_visibility_before_click():
    """Когда строка не видна, worker должен найти её проходом по таблице и потом кликнуть."""
    from run_enable_worker import execute_enable_via_playwright

    toggle = FakeEnableToggle("false")
    cell = FakeEnableCell(toggle)
    page = FakeEnablePage(None)

    async def mock_human_click(_page, element, double_check_pause=True):
        element.aria_checked = "true"

    with (
        patch("run_enable_worker.asyncio.sleep", new=AsyncMock()),
        patch(
            "run_enable_worker.find_toggle_cell_with_table_scan", new=AsyncMock(return_value=cell)
        ) as scan_mock,
        patch(
            "run_enable_worker.human_click", new=AsyncMock(side_effect=mock_human_click)
        ) as human_click,
        patch(
            "run_enable_worker._wait_for_enable_confirmation",
            new=AsyncMock(
                return_value=(
                    True,
                    "Объявление включено: переключатель дважды подтвердил состояние ON",
                )
            ),
        ),
    ):
        success, message = await execute_enable_via_playwright(page, "123456")

    assert success is True
    assert "подтвердил состояние ON" in message
    scan_mock.assert_awaited_once_with(
        page,
        "123456",
        reset_to_top=True,
        max_scroll_passes=120,
        step_px=220,
        fallback_max_steps=60,
    )
    human_click.assert_awaited_once_with(page, toggle, double_check_pause=True)


# Тест: если Meta не подтвердила ON, задача не должна считаться успешной.
@pytest.mark.asyncio
async def test_execute_enable_returns_failure_when_on_is_not_confirmed():
    """Если интерфейс не подтвердил ON, worker должен вернуть ошибку, а не ложный успех."""
    from run_enable_worker import execute_enable_via_playwright

    toggle = FakeEnableToggle("false")
    page = FakeEnablePage(FakeEnableCell(toggle))

    with (
        patch("run_enable_worker.asyncio.sleep", new=AsyncMock()),
        patch("run_enable_worker.human_click", new=AsyncMock()) as human_click,
        patch(
            "run_enable_worker._wait_for_enable_confirmation",
            new=AsyncMock(
                return_value=(
                    False,
                    "Переключатель нажат, но интерфейс Meta не подтвердил состояние ON",
                )
            ),
        ),
    ):
        success, message = await execute_enable_via_playwright(page, "123456")

    assert success is False
    assert "не подтвердил состояние ON" in message
    human_click.assert_awaited_once_with(page, toggle, double_check_pause=True)


# Тест: ошибка закрытой страницы должна считаться browser-сбоем для reconnect-контура.
def test_is_browser_connection_error_matches_closed_page_runtime():
    """Закрытая вкладка должна запускать reconnect, а не обычный retry."""
    from run_enable_worker import _is_browser_connection_error

    assert _is_browser_connection_error(
        RuntimeError("Page.query_selector: Target page, context or browser has been closed")
    )


# Тест: browser-ошибка внутри enable loop должна подниматься наружу на переподключение.
@pytest.mark.asyncio
async def test_enable_worker_loop_reraises_browser_connection_error():
    """Потеря страницы не должна маскироваться под RETRYING внутри того же page-объекта."""
    from run_enable_worker import enable_worker_loop

    task = type(
        "Task",
        (),
        {
            "id": "task-1",
            "fb_ad_id": "123456",
            "ad_name": "Ad 1",
            "attempt_count": 1,
            "max_attempts": 10,
            "requested_by_username": "dashboard",
        },
    )()

    fb_ad = type(
        "Ad",
        (),
        {
            "fb_ad_id": "123456",
            "ad_name": "Ad 1",
        },
    )()

    task = type(
        "Task",
        (),
        {
            "id": "task-1",
            "fb_ad_id": "123456",
            "ad_name": "Ad 1",
            "attempt_count": 1,
            "max_attempts": 10,
            "requested_by_username": "dashboard",
            "fb_ad": fb_ad,
        },
    )()

    browser_error = RuntimeError(
        "Page.query_selector: Target page, context or browser has been closed"
    )

    with (
        patch(
            "run_enable_worker.execute_enable_via_playwright",
            new=AsyncMock(side_effect=browser_error),
        ),
        patch(
            "run_enable_worker._resolve_ads_manager_page",
            new=AsyncMock(return_value=(object(), None)),
        ),
        patch(
            "run_enable_worker.claim_next_task",
            new=AsyncMock(side_effect=[task, browser_error]),
        ),
    ):
        with pytest.raises(RuntimeError, match="Target page, context or browser has been closed"):
            await enable_worker_loop(
                manager=object(),
                tg_client=None,
                tg_chat_id="",
                poll_interval=0,
                shutdown_event=None,
                send_completion_callback=None,
            )


# Тест: таймаут браузерной операции должен переводить задачу в retry и требовать reconnect.
@pytest.mark.asyncio
async def test_enable_worker_loop_marks_retrying_on_timeout():
    """Таймаут не должен оставлять задачу в RUNNING: воркер обязан вернуть её в RETRYING."""
    from run_enable_worker import EnableBrowserOperationTimeoutError, enable_worker_loop

    fb_ad = type(
        "Ad",
        (),
        {
            "fb_ad_id": "654321",
            "ad_name": "Ad 2",
        },
    )()

    task = type(
        "Task",
        (),
        {
            "id": "task-2",
            "fb_ad_id": "654321",
            "ad_name": "Ad 2",
            "attempt_count": 1,
            "max_attempts": 10,
            "requested_by_username": "dashboard",
            "fb_ad": fb_ad,
        },
    )()

    timeout_error = EnableBrowserOperationTimeoutError(
        "Браузерная операция включения превысила таймаут"
    )

    with (
        patch(
            "run_enable_worker.execute_enable_via_playwright",
            new=AsyncMock(side_effect=asyncio.TimeoutError()),
        ),
        patch(
            "run_enable_worker._resolve_ads_manager_page",
            new=AsyncMock(return_value=(object(), None)),
        ),
        patch(
            "run_enable_worker.claim_next_task",
            new=AsyncMock(side_effect=[task, timeout_error]),
        ),
        patch("run_enable_worker.mark_retrying", new=AsyncMock()) as mark_retrying,
        patch("run_enable_worker.mark_failed", new=AsyncMock()) as mark_failed,
    ):
        with pytest.raises(
            EnableBrowserOperationTimeoutError,
            match="Браузерная операция включения превысила таймаут",
        ):
            await enable_worker_loop(
                manager=object(),
                tg_client=None,
                tg_chat_id="",
                poll_interval=0,
                shutdown_event=None,
                send_completion_callback=None,
            )

    mark_retrying.assert_awaited_once()
    mark_failed.assert_not_awaited()
