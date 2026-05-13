# -*- coding: utf-8 -*-
"""Тесты для модуля campaign_recorder."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- Task 1: CdpSession ---


@pytest.mark.asyncio
async def test_cdp_session_returns_page():
    """cdp_session.connect() должен вернуть CDP-страницу Vision через gRPC-клиент."""
    mock_page = MagicMock()
    mock_page.url = "https://adsmanager.facebook.com"

    mock_client = MagicMock(
        start=AsyncMock(),
        start_browser=AsyncMock(),
        disconnect_browser=AsyncMock(),
        close=AsyncMock(),
        cdp_url="http://localhost:9222",
    )

    mock_browser = MagicMock(
        contexts=[MagicMock(pages=[mock_page])],
        close=AsyncMock(),
    )

    with (
        patch(
            "core.campaign_recorder.cdp_session._make_browser_client",
            return_value=mock_client,
        ),
        patch("core.campaign_recorder.cdp_session.async_playwright") as mock_pw,
    ):
        mock_pw.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(
                chromium=MagicMock(connect_over_cdp=AsyncMock(return_value=mock_browser))
            )
        )
        mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

        from core.campaign_recorder.cdp_session import CdpSession

        session = CdpSession()
        async with session.connect() as page:
            assert page is mock_page

        mock_client.start.assert_awaited_once()
        mock_client.start_browser.assert_awaited_once()


# --- Task 2: EventInjector ---


def test_injector_js_contains_event_listeners():
    """JS-сниппет должен слушать click, input, change, keydown, submit и проставлять session_id."""
    from core.campaign_recorder.event_injector import BUILD_JS_INJECTOR

    js = BUILD_JS_INJECTOR("test-session-id")
    for event in ["click", "input", "change", "keydown", "submit"]:
        assert event in js, f"JS не содержит обработчик события {event}"
    assert "test-session-id" in js
    assert "session_id" in js


def test_injector_js_collects_extended_fields():
    """JS должен собирать label_text, placeholder, nearest_heading, selector_candidates."""
    from core.campaign_recorder.event_injector import BUILD_JS_INJECTOR

    js = BUILD_JS_INJECTOR("sid")
    for fragment in [
        "label_text",
        "placeholder",
        "nearest_heading",
        "selector_candidates",
    ]:
        assert fragment in js, f"JS не содержит {fragment}"


@pytest.mark.asyncio
async def test_injector_injects_into_page():
    """inject_event_listener должен вызвать evaluate на каждом фрейме страницы."""
    mock_frame = MagicMock(evaluate=AsyncMock(), url="about:blank")
    mock_page = MagicMock(frames=[mock_frame])
    from core.campaign_recorder.event_injector import inject_event_listener

    await inject_event_listener(mock_page)
    mock_frame.evaluate.assert_awaited_once()


# --- Task 3: SessionWriter ---


def test_session_writer_creates_file():
    """SessionWriter должен создать JSON-файл при закрытии."""
    from core.campaign_recorder.session_writer import SessionWriter

    with tempfile.TemporaryDirectory() as tmpdir:
        writer = SessionWriter(offer_code="DRC_CR2", recordings_dir=Path(tmpdir))
        writer.add_events([{"type": "click", "ts": 1.0, "tag": "button"}])
        path = writer.save()
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["offer_code"] == "DRC_CR2"
        assert len(data["events"]) == 1


def test_session_writer_filename_contains_offer():
    """Имя файла должно содержать код оффера."""
    from core.campaign_recorder.session_writer import SessionWriter

    with tempfile.TemporaryDirectory() as tmpdir:
        writer = SessionWriter(offer_code="DRC_CR2", recordings_dir=Path(tmpdir))
        writer.add_events([])
        path = writer.save()
        assert "DRC_CR2" in path.name
