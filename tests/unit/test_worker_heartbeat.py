# -*- coding: utf-8 -*-
"""Unit-тесты для update_worker_heartbeat из core/observer/runtime_status.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.observer.runtime_status import update_worker_heartbeat

# Вспомогательная фикстура: создаёт мок async-сессии с execute и commit


def _make_session():
    """Возвращает мок async-контекст-менеджера БД-сессии."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, session


# Сценарий: вызов update_worker_heartbeat выполняет INSERT ... ON CONFLICT upsert
@pytest.mark.asyncio
async def test_update_worker_heartbeat_calls_execute():
    ctx, session = _make_session()
    factory = MagicMock(return_value=ctx)

    with patch("core.observer.runtime_status.get_session_factory", return_value=factory):
        await update_worker_heartbeat("disable", status="running", message="Тестовое сообщение")

    session.execute.assert_called_once()
    session.commit.assert_called_once()


# Сценарий: повторный вызов не дублирует записи (upsert идёт по worker_name)
@pytest.mark.asyncio
async def test_update_worker_heartbeat_second_call_also_executes():
    ctx1, session1 = _make_session()
    ctx2, session2 = _make_session()
    calls = [ctx1, ctx2]
    factory = MagicMock(side_effect=calls)

    with patch("core.observer.runtime_status.get_session_factory", return_value=factory):
        await update_worker_heartbeat("enable", status="idle")
        await update_worker_heartbeat("enable", status="running")

    # Оба вызова должны пройти execute+commit
    session1.execute.assert_called_once()
    session2.execute.assert_called_once()


# Сценарий: при недоступной БД функция не поднимает исключение
@pytest.mark.asyncio
async def test_update_worker_heartbeat_swallows_db_error():
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(side_effect=OSError("Нет подключения к БД"))
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)

    with patch("core.observer.runtime_status.get_session_factory", return_value=factory):
        # Не должно бросать исключение
        await update_worker_heartbeat("health_watchdog", status="running")
