# -*- coding: utf-8 -*-
"""Тесты daily digest: render_digest_message, get_digest_data, run_digest_scheduler."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =========================================================
# ЧАСТЬ 1: render_digest_message
# =========================================================


def _make_offer(code: str, spend: float, leads: int, deps: int, delta: float | None = None):
    """Вспомогательная фабрика для записи оффера в дайджест."""
    return {
        "code": code,
        "spend": Decimal(str(spend)),
        "leads": leads,
        "deps": deps,
        "delta_pct": delta,
    }


def test_render_empty_data():
    """Пустые данные → сообщение об отсутствии активности."""
    from core.telegram.digest import render_digest_message

    data = {
        "top_offers": [],
        "wasted_alerts": 0,
        "new_offers": [],
        "totals": {"spend": Decimal("0"), "leads": 0, "deps": 0},
        "date_str": "01.01.2025",
    }
    text = render_digest_message(data)
    assert "Daily digest" in text
    assert "Активности" in text
    assert "01.01.2025" in text


def test_render_top3_with_positive_delta():
    """Топ-3 офферов с положительной дельтой → стрелка ▲."""
    from core.telegram.digest import render_digest_message

    data = {
        "top_offers": [
            _make_offer("DRC_CR2", 500.0, 10, 2, delta=12.5),
            _make_offer("CR_X", 300.0, 5, 1, delta=-8.0),
            _make_offer("NEW_ONE", 100.0, 2, 0, delta=None),
        ],
        "wasted_alerts": 3,
        "new_offers": ["NEW_ONE"],
        "totals": {"spend": Decimal("900"), "leads": 17, "deps": 3},
        "date_str": "22.05.2025",
    }
    text = render_digest_message(data)

    # Топ офферов
    assert "DRC_CR2" in text
    assert "CR_X" in text
    assert "NEW_ONE" in text

    # Дельты
    assert "▲" in text  # DRC_CR2 +12.5%
    assert "▼" in text  # CR_X -8.0%

    # Числа
    assert "$500" in text
    assert "$300" in text

    # Алёрты
    assert "3" in text
    assert "алёрт" in text

    # Новые офферы
    assert "+1" in text
    assert "NEW_ONE" in text


def test_render_negative_delta():
    """Отрицательная дельта → стрелка ▼ с корректным abs."""
    from core.telegram.digest import render_digest_message

    data = {
        "top_offers": [_make_offer("DRC", 100.0, 2, 0, delta=-33.3)],
        "wasted_alerts": 0,
        "new_offers": [],
        "totals": {"spend": Decimal("100"), "leads": 2, "deps": 0},
        "date_str": "20.05.2025",
    }
    text = render_digest_message(data)
    assert "▼" in text
    assert "▲" not in text
    assert "33%" in text


def test_render_no_delta():
    """Нет дельты (первый день оффера) → стрелок нет."""
    from core.telegram.digest import render_digest_message

    data = {
        "top_offers": [_make_offer("NEW_OFF", 50.0, 1, 0, delta=None)],
        "wasted_alerts": 0,
        "new_offers": [],
        "totals": {"spend": Decimal("50"), "leads": 1, "deps": 0},
        "date_str": "10.05.2025",
    }
    text = render_digest_message(data)
    assert "▲" not in text
    assert "▼" not in text


def test_alerts_noun_plural():
    """Склонение 'алёртов' для числа > 4."""
    from core.telegram.digest import _alerts_noun

    assert _alerts_noun(1) == "алёрт"
    assert _alerts_noun(2) == "алёрта"
    assert _alerts_noun(5) == "алёртов"
    assert _alerts_noun(11) == "алёртов"
    assert _alerts_noun(21) == "алёрт"


# =========================================================
# ЧАСТЬ 2: get_digest_data — моки сессии
# =========================================================


@pytest.mark.asyncio
async def test_get_digest_data_returns_keys():
    """get_digest_data возвращает все ожидаемые ключи при пустой БД."""
    from core.telegram.digest_queries import get_digest_data

    # Мокаем сессию — все запросы возвращают пустые результаты
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_result.scalar.return_value = 0
    mock_result.one.return_value = MagicMock(spend=None, leads=None, deps=None)
    mock_session.execute.return_value = mock_result

    now = datetime(2025, 5, 22, 10, 0, 0, tzinfo=UTC)
    data = await get_digest_data(mock_session, now=now, tz_name="Europe/Moscow")

    assert "top_offers" in data
    assert "wasted_alerts" in data
    assert "new_offers" in data
    assert "totals" in data
    assert "date_str" in data


@pytest.mark.asyncio
async def test_get_digest_data_date_str_yesterday():
    """date_str должен быть «вчера» относительно now."""
    from core.telegram.digest_queries import get_digest_data

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_result.scalar.return_value = 0
    mock_result.one.return_value = MagicMock(spend=None, leads=None, deps=None)
    mock_session.execute.return_value = mock_result

    # now = 23.05.2025 → yesterday = 22.05.2025
    now = datetime(2025, 5, 23, 9, 0, 0, tzinfo=UTC)
    data = await get_digest_data(mock_session, now=now, tz_name="UTC")
    assert data["date_str"] == "22.05.2025"


# =========================================================
# ЧАСТЬ 3: run_digest_scheduler — моки времени
# =========================================================


@pytest.mark.asyncio
async def test_digest_scheduler_sends_at_correct_hour():
    """Scheduler отправляет digest через _send_digest, возвращает True.

    Патчим источники через их оригинальные пути (lazy-imports внутри функции).
    """
    from core.telegram.digest_scheduler import _send_digest

    client = AsyncMock()
    client.send_message = AsyncMock()

    mock_session = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory_fn = MagicMock(return_value=mock_ctx)

    digest_data = {
        "date_str": "22.05.2025",
        "top_offers": [],
        "wasted_alerts": 0,
        "new_offers": [],
        "totals": {"spend": Decimal("0"), "leads": 0, "deps": 0},
    }

    with (
        patch("core.db.get_session_factory", return_value=mock_factory_fn),
        patch(
            "core.telegram.digest_queries.get_digest_data",
            new_callable=AsyncMock,
            return_value=digest_data,
        ),
        patch(
            "core.telegram.digest.render_digest_message",
            return_value="📊 Daily digest — 22.05.2025",
        ),
    ):
        now = datetime(2025, 5, 23, 6, 0, 0, tzinfo=UTC)  # 9:00 Moscow
        result = await _send_digest(client, chat_id="-1001", now=now, tz="Europe/Moscow")

    assert result is True
    client.send_message.assert_called_once()
    call_kwargs = client.send_message.call_args[1]
    assert call_kwargs["chat_id"] == "-1001"
    assert "Daily digest" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_digest_scheduler_no_double_send():
    """Scheduler не отправляет digest дважды в одну дату."""
    from core.telegram.digest_scheduler import run_digest_scheduler

    client = AsyncMock()
    sent_calls: list[datetime] = []

    async def fake_send_digest(c, *, chat_id, now, tz):
        sent_calls.append(now)
        return True

    tick = 0

    async def fake_sleep(n):
        nonlocal tick
        tick += 1
        if tick >= 3:
            raise asyncio.CancelledError()

    # Время: дважды 9:00 → должен отправить только один раз
    tz_str = "UTC"
    hours_seq = [9, 9, 9]

    with (
        patch("core.telegram.digest_scheduler._send_digest", new=fake_send_digest),
        patch("asyncio.sleep", new=fake_sleep),
        patch("core.telegram.digest_scheduler.datetime") as mock_dt,
        # Изолируем тест от БД: персистентность digest_last_sent_date проверяется отдельно.
        patch(
            "core.telegram.digest_scheduler._load_last_sent_date",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "core.telegram.digest_scheduler._save_last_sent_date",
            new=AsyncMock(),
        ),
    ):
        call_num = [0]

        def fake_now(tz=None):
            h = hours_seq[min(call_num[0], len(hours_seq) - 1)]
            call_num[0] += 1
            return datetime(2025, 5, 22, h, 0, 0, tzinfo=UTC)

        mock_dt.now.side_effect = fake_now

        with pytest.raises(asyncio.CancelledError):
            await run_digest_scheduler(client, "-100", tz=tz_str, hour=9, check_interval=0)

    # Отправлено ровно один раз за одни и те же сутки
    assert len(sent_calls) == 1
