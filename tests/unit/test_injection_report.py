"""Тест: attach_recorder возвращает InjectionReport с проверкой sanity-check."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_attach_recorder_returns_injection_report():
    from core.campaign_recorder.event_injector import attach_recorder

    frame_ok = MagicMock(url="https://adsmanager.facebook.com/x")
    frame_ok.evaluate = AsyncMock(return_value=True)
    frame_bad = MagicMock(url="https://adsmanager.facebook.com/y")
    # последовательность: sanity_check (False) → re-inject (None) → sanity_check (False)
    frame_bad.evaluate = AsyncMock(side_effect=[False, None, False])

    page = MagicMock(
        url="https://adsmanager.facebook.com",
        frames=[frame_ok, frame_bad],
    )
    page.on = MagicMock()
    context = MagicMock(pages=[page])
    context.add_init_script = AsyncMock()
    context.on = MagicMock()

    report = await attach_recorder(context, session_id="sid-1")
    assert report.pages, "InjectionReport должен содержать страницы"
    p = report.pages[0]
    assert p.url == "https://adsmanager.facebook.com"
    assert p.frames_total == 2
    assert p.frames_injected == 1
