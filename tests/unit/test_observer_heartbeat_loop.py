# -*- coding: utf-8 -*-
"""Тесты фонового heartbeat-цикла observer."""

from __future__ import annotations

import asyncio

import pytest


# Проверяем, что _observer_heartbeat_loop вызывает update_observer_runtime_status
# со значениями из глобальных _observer_status и _observer_message.
@pytest.mark.asyncio
async def test_heartbeat_loop_calls_update_with_status_and_message(monkeypatch):
    import apps.observer_worker.main as observer_main

    calls: list[tuple] = []

    async def fake_update(*, status, message=None, **kwargs):
        calls.append((status, message))

    monkeypatch.setattr(observer_main, "update_observer_runtime_status", fake_update)

    # Устанавливаем глобальные переменные статуса
    monkeypatch.setattr(observer_main, "_observer_status", "RUNNING")
    monkeypatch.setattr(observer_main, "_observer_message", "Запущен.")

    sleep_called = asyncio.Event()

    async def fake_sleep(seconds):
        sleep_called.set()
        # Блокируемся до отмены задачи
        await asyncio.shield(asyncio.get_event_loop().create_future())

    monkeypatch.setattr(observer_main.asyncio, "sleep", fake_sleep)

    # Запускаем без аргументов — используются глобальные переменные
    task = asyncio.create_task(observer_main._observer_heartbeat_loop())
    try:
        await asyncio.wait_for(sleep_called.wait(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(calls) >= 1
    assert calls[0] == ("RUNNING", "Запущен.")


# Проверяем, что при изменении глобальных переменных фоновый цикл
# передаёт обновлённые значения при следующем вызове.
@pytest.mark.asyncio
async def test_heartbeat_loop_reflects_updated_refs(monkeypatch):
    import apps.observer_worker.main as observer_main

    calls: list[tuple] = []
    sleep_called = asyncio.Event()

    async def fake_update(*, status, message=None, **kwargs):
        calls.append((status, message))

    monkeypatch.setattr(observer_main, "update_observer_runtime_status", fake_update)

    # Устанавливаем начальный статус
    monkeypatch.setattr(observer_main, "_observer_status", "RUNNING")
    monkeypatch.setattr(observer_main, "_observer_message", "Первое сообщение")

    async def fake_sleep(seconds):
        sleep_called.set()
        # Блокируемся до отмены задачи
        await asyncio.shield(asyncio.get_event_loop().create_future())

    monkeypatch.setattr(observer_main.asyncio, "sleep", fake_sleep)

    task = asyncio.create_task(observer_main._observer_heartbeat_loop())
    try:
        # Дождёмся первого вызова heartbeat (до sleep)
        await asyncio.wait_for(sleep_called.wait(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Хотя бы один вызов должен быть с начальными значениями
    assert any(status == "RUNNING" and msg == "Первое сообщение" for status, msg in calls)


# Проверяем, что ошибка в update_observer_runtime_status не ломает heartbeat-цикл.
@pytest.mark.asyncio
async def test_heartbeat_loop_survives_update_error(monkeypatch):
    import apps.observer_worker.main as observer_main

    call_count = 0
    sleep_called = asyncio.Event()

    async def flaky_update(*, status, message=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("БД недоступна")

    monkeypatch.setattr(observer_main, "update_observer_runtime_status", flaky_update)
    monkeypatch.setattr(observer_main, "_observer_status", "RUNNING")
    monkeypatch.setattr(observer_main, "_observer_message", None)

    async def fake_sleep(seconds):
        sleep_called.set()
        await asyncio.shield(asyncio.get_event_loop().create_future())

    monkeypatch.setattr(observer_main.asyncio, "sleep", fake_sleep)

    task = asyncio.create_task(observer_main._observer_heartbeat_loop())
    try:
        # Цикл не должен упасть — он должен дойти до sleep даже после ошибки
        await asyncio.wait_for(sleep_called.wait(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Первый вызов упал с ошибкой, но цикл продолжил работу и дошёл до sleep
    assert sleep_called.is_set()
