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
    """cdp_session.connect() должен вернуть CDP-страницу Vision."""
    mock_page = MagicMock()
    mock_page.url = "https://adsmanager.facebook.com"

    with patch("core.campaign_recorder.cdp_session.async_playwright") as mock_pw:
        mock_browser = MagicMock(
            contexts=[MagicMock(pages=[mock_page])],
            close=AsyncMock(),
        )
        mock_pw.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(
                chromium=MagicMock(
                    connect_over_cdp=AsyncMock(return_value=mock_browser)
                )
            )
        )
        from core.campaign_recorder.cdp_session import CdpSession

        session = CdpSession(cdp_url="http://localhost:9222")
        async with session.connect() as page:
            assert page is mock_page


# --- Task 2: EventInjector ---


def test_injector_js_contains_event_listeners():
    """JS-сниппет должен слушать click, input, change, select, focus."""
    from core.campaign_recorder.event_injector import BUILD_JS_INJECTOR

    js = BUILD_JS_INJECTOR()
    for event in ["click", "input", "change", "select", "focus"]:
        assert event in js, f"JS не содержит обработчик события {event}"


@pytest.mark.asyncio
async def test_injector_injects_into_page():
    """inject_event_listener должен вызвать evaluate на странице."""
    mock_page = AsyncMock()
    from core.campaign_recorder.event_injector import inject_event_listener

    await inject_event_listener(mock_page)
    mock_page.evaluate.assert_called_once()


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


# --- Task 4: Analyzer ---


def test_analyzer_counts_event_types():
    """Анализатор должен подсчитать количество каждого типа событий."""
    from core.campaign_recorder.analyzer import analyze_session

    events = [
        {
            "type": "click",
            "tag": "button",
            "text": "Создать",
            "classes": ["btn"],
            "data_attrs": {},
            "id": "",
            "role": None,
            "aria_label": None,
            "xpath": "//button[1]",
            "value": None,
        },
        {
            "type": "click",
            "tag": "div",
            "text": "Конверсии",
            "classes": [],
            "data_attrs": {},
            "id": "",
            "role": "option",
            "aria_label": None,
            "xpath": "//div[2]",
            "value": None,
        },
        {
            "type": "input",
            "tag": "input",
            "text": "",
            "classes": [],
            "data_attrs": {},
            "id": "campaign_name",
            "role": None,
            "aria_label": "Название кампании",
            "xpath": "//input[1]",
            "value": "MV | DRC",
        },
    ]
    report = analyze_session({"offer_code": "DRC_CR2", "events": events})
    assert report["total_events"] == 3
    assert report["by_type"]["click"] == 2
    assert report["by_type"]["input"] == 1
    assert len(report["stable_selectors"]) > 0


def test_analyzer_detects_stable_selectors():
    """Элементы с aria-label или id считаются стабильными селекторами."""
    from core.campaign_recorder.analyzer import analyze_session

    events = [
        {
            "type": "input",
            "tag": "input",
            "id": "campaign_name",
            "aria_label": "Название",
            "classes": [],
            "data_attrs": {},
            "text": "",
            "role": None,
            "xpath": "//input[1]",
            "value": "test",
        },
        {
            "type": "click",
            "tag": "button",
            "id": "",
            "aria_label": None,
            "classes": ["_abc123"],
            "data_attrs": {},
            "text": "",
            "role": None,
            "xpath": "//button[1]",
            "value": None,
        },
    ]
    report = analyze_session({"offer_code": "DRC_CR2", "events": events})
    stable = {s["selector"] for s in report["stable_selectors"]}
    assert any("campaign_name" in s for s in stable)
