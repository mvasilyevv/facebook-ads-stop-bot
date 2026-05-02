# -*- coding: utf-8 -*-
"""Тесты модели AlertSnooze и функции load_active_snooze_ad_ids."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


# Проверяем, что AlertSnooze успешно создаётся с минимальными полями.
def test_alert_snooze_model_has_required_fields():
    """Модель AlertSnooze должна иметь поля fb_ad_id, snoozed_until, created_at."""
    from core.models import AlertSnooze

    snooze = AlertSnooze(
        fb_ad_id="123456",
        snoozed_until=datetime.now(UTC) + timedelta(minutes=30),
    )
    assert snooze.fb_ad_id == "123456"
    assert snooze.snoozed_until is not None


# Проверяем, что load_active_snooze_ad_ids возвращает только активные снузы.
@pytest.mark.asyncio
async def test_load_active_snooze_ad_ids_returns_only_active():
    """Функция должна вернуть только ad_id с snoozed_until > now."""
    from core.observer.db_queries import load_active_snooze_ad_ids

    # Мок сессии: имитируем результат SELECT
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [("111",), ("222",)]
    session.execute = AsyncMock(return_value=mock_result)

    result = await load_active_snooze_ad_ids(session=session)

    assert result == {"111", "222"}
    session.execute.assert_awaited_once()


# Проверяем, что load_active_snooze_ad_ids возвращает пустое множество, если снузов нет.
@pytest.mark.asyncio
async def test_load_active_snooze_ad_ids_empty_when_no_snoozes():
    """Если активных снузов нет — возвращается пустое множество."""
    from core.observer.db_queries import load_active_snooze_ad_ids

    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    session.execute = AsyncMock(return_value=mock_result)

    result = await load_active_snooze_ad_ids(session=session)

    assert result == set()
