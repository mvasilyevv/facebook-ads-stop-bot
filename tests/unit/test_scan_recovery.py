# -*- coding: utf-8 -*-
"""Тесты восстановления скана через перезагрузку страницы."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.scanner.recovery import ScanDataUnavailableError, scan_ads_with_page_recovery


# Проверяем что штатный скан идёт через reset -> refresh -> parse и не запускает recovery.
@pytest.mark.asyncio
async def test_scan_ads_with_page_recovery_runs_regular_scan_first():
    """При наличии строк helper не должен запускать page reload."""
    page = AsyncMock()
    call_order: list[str] = []
    row = SimpleNamespace(fb_ad_id="ad-1", ad_name="Ad 1")

    async def reset_scroll(_page):
        call_order.append("reset")

    async def refresh_table(_page):
        call_order.append("refresh")
        return True

    async def scroll_and_parse(_page, _parse_fn):
        call_order.append("scroll")
        return [row]

    rows = await scan_ads_with_page_recovery(
        page=page,
        parse_fn=AsyncMock(),
        refresh_table_fn=refresh_table,
        reset_scroll_fn=reset_scroll,
        scroll_and_parse_fn=scroll_and_parse,
        sleep_fn=AsyncMock(),
        settle_delay_seconds=0,
    )

    assert rows == [row]
    assert call_order == ["reset", "refresh", "scroll"]
    page.reload.assert_not_awaited()


# Проверяем что recovery перезагружает страницу и восстанавливает скан на первой удачной попытке.
@pytest.mark.asyncio
async def test_scan_ads_with_page_recovery_recovers_after_reload():
    """После пустого первого скана helper должен сделать reload и повторить парсинг."""
    page = AsyncMock()
    sleep_mock = AsyncMock()
    on_recovery_attempt = AsyncMock()
    call_order: list[str] = []
    row = SimpleNamespace(fb_ad_id="ad-2", ad_name="Ad 2")
    scroll_and_parse = AsyncMock(side_effect=[[], [row]])

    async def reset_scroll(_page):
        call_order.append("reset")

    async def refresh_table(_page):
        call_order.append("refresh")
        return True

    rows = await scan_ads_with_page_recovery(
        page=page,
        parse_fn=AsyncMock(),
        refresh_table_fn=refresh_table,
        reset_scroll_fn=reset_scroll,
        scroll_and_parse_fn=scroll_and_parse,
        sleep_fn=sleep_mock,
        settle_delay_seconds=0,
        retry_interval_seconds=60,
        on_recovery_attempt=on_recovery_attempt,
    )

    assert rows == [row]
    assert call_order == ["reset", "refresh", "reset"]
    page.reload.assert_awaited_once_with(wait_until="domcontentloaded")
    on_recovery_attempt.assert_awaited_once_with(1, 5)
    sleep_mock.assert_not_awaited()


# Проверяем что после исчерпания лимита helper выбрасывает фатальную ошибку недоступности данных.
@pytest.mark.asyncio
async def test_scan_ads_with_page_recovery_raises_after_max_attempts():
    """Если строки не появились ни разу, helper должен завершиться ScanDataUnavailableError."""
    page = AsyncMock()
    sleep_mock = AsyncMock()
    scroll_and_parse = AsyncMock(return_value=[])

    with pytest.raises(ScanDataUnavailableError) as exc_info:
        await scan_ads_with_page_recovery(
            page=page,
            parse_fn=AsyncMock(),
            refresh_table_fn=AsyncMock(return_value=True),
            reset_scroll_fn=AsyncMock(),
            scroll_and_parse_fn=scroll_and_parse,
            sleep_fn=sleep_mock,
            settle_delay_seconds=0,
            max_recovery_attempts=3,
            retry_interval_seconds=15,
        )

    assert "3 попыток" in str(exc_info.value)
    assert page.reload.await_count == 3
    assert sleep_mock.await_count == 2
