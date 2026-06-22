# -*- coding: utf-8 -*-
"""Тесты _sleep_with_runtime_refresh: освежение observer:runtime на длинных интервалах.

Регресс-защита: адаптивный IDLE-интервал может превысить watchdog-порог staleness (300с)
и TTL ключа (360с). Без чанкования sleep это вернуло бы ложный watchdog-алерт (PR #17).
"""

from __future__ import annotations

import asyncio

import pytest

import apps.observer_worker.main as m


# Длинный sleep (300с) бьётся на чанки по RUNTIME_REFRESH_SECONDS (120) → между чанками
# observer:runtime переписывается, чтобы updated_at оставался свежим.
@pytest.mark.asyncio
async def test_long_sleep_chunks_and_refreshes(monkeypatch):
    waits: list[float] = []
    refreshes: list[dict] = []

    async def fake_wait(*events, seconds):
        waits.append(seconds)

    async def fake_publish(redis_client, **kwargs):
        refreshes.append(kwargs)

    monkeypatch.setattr(m, "_wait_interruptible", fake_wait)
    monkeypatch.setattr(m, "_publish_runtime_status", fake_publish)
    monkeypatch.setattr(m, "RUNTIME_REFRESH_SECONDS", 120)

    ev1, ev2 = asyncio.Event(), asyncio.Event()  # не выставлены
    await m._sleep_with_runtime_refresh(None, ev1, ev2, seconds=300.0)

    # 300 = 120 + 120 + 60 → три чанка.
    assert waits == [120.0, 120.0, 60.0]
    # Стартовая публикация при входе (UI сразу получает next_scan_at/режим) + освежения
    # после 1-го и 2-го чанка (после 3-го remaining=0 → не освежаем) = 1 + 2 = 3.
    assert len(refreshes) == 3
    # Все публикации помечены idle (между сканами).
    assert all(r.get("status") == "idle" for r in refreshes)


# Короткий sleep (< RUNTIME_REFRESH) — один чанк. Стартовая публикация при входе есть
# (иначе next_scan_at/scan_mode не доезжали на CALM 90с < 120с), промежуточных нет.
@pytest.mark.asyncio
async def test_short_sleep_no_intermediate_refresh(monkeypatch):
    waits: list[float] = []
    refreshes: list[dict] = []

    async def fake_wait(*events, seconds):
        waits.append(seconds)

    async def fake_publish(redis_client, **kwargs):
        refreshes.append(kwargs)

    monkeypatch.setattr(m, "_wait_interruptible", fake_wait)
    monkeypatch.setattr(m, "_publish_runtime_status", fake_publish)
    monkeypatch.setattr(m, "RUNTIME_REFRESH_SECONDS", 120)

    ev = asyncio.Event()
    await m._sleep_with_runtime_refresh(None, ev, seconds=90.0)

    assert waits == [90.0]
    # Ровно одна публикация — стартовая при входе; промежуточных нет (после чанка remaining=0).
    assert len(refreshes) == 1


# Если event (trigger/shutdown) выставлен во время чанка — немедленный возврат без
# дальнейшего освежения (scan-now прерывает sleep сразу, прерываемость сохранена).
@pytest.mark.asyncio
async def test_event_set_returns_early(monkeypatch):
    ev = asyncio.Event()
    refreshes: list[dict] = []

    async def fake_wait(*events, seconds):
        # Имитируем приход trigger'а во время первого чанка.
        ev.set()

    async def fake_publish(redis_client, **kwargs):
        refreshes.append(kwargs)

    monkeypatch.setattr(m, "_wait_interruptible", fake_wait)
    monkeypatch.setattr(m, "_publish_runtime_status", fake_publish)
    monkeypatch.setattr(m, "RUNTIME_REFRESH_SECONDS", 120)

    await m._sleep_with_runtime_refresh(None, ev, seconds=300.0)

    # Стартовая публикация при входе успевает, затем event прерывает первый чанк →
    # дальнейших освежений нет. Итого ровно одна публикация (стартовая).
    assert len(refreshes) == 1


# next_scan_at пробрасывается в каждое освежение (для UI обратного отсчёта).
@pytest.mark.asyncio
async def test_next_scan_at_propagated(monkeypatch):
    from datetime import datetime, timezone

    refreshes: list[dict] = []

    async def fake_wait(*events, seconds):
        pass

    async def fake_publish(redis_client, **kwargs):
        refreshes.append(kwargs)

    monkeypatch.setattr(m, "_wait_interruptible", fake_wait)
    monkeypatch.setattr(m, "_publish_runtime_status", fake_publish)
    monkeypatch.setattr(m, "RUNTIME_REFRESH_SECONDS", 120)

    ev = asyncio.Event()
    nsa = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    await m._sleep_with_runtime_refresh(None, ev, seconds=300.0, next_scan_at=nsa)

    assert refreshes  # были освежения
    assert all(r.get("next_scan_at") == nsa for r in refreshes)


# scan_mode (режим адаптивного скана) пробрасывается в каждую публикацию — для UI-индикатора
# ScanModeBar (линия зелёный→красный показывает CRITICAL/ELEVATED/CALM/IDLE).
@pytest.mark.asyncio
async def test_scan_mode_propagated(monkeypatch):
    refreshes: list[dict] = []

    async def fake_wait(*events, seconds):
        pass

    async def fake_publish(redis_client, **kwargs):
        refreshes.append(kwargs)

    monkeypatch.setattr(m, "_wait_interruptible", fake_wait)
    monkeypatch.setattr(m, "_publish_runtime_status", fake_publish)
    monkeypatch.setattr(m, "RUNTIME_REFRESH_SECONDS", 120)

    ev = asyncio.Event()
    await m._sleep_with_runtime_refresh(None, ev, seconds=300.0, scan_mode="ELEVATED")

    assert refreshes  # были публикации
    assert all(r.get("scan_mode") == "ELEVATED" for r in refreshes)


# На паузе освежение сохраняет status="paused" — не затирает его ложным "idle/running".
@pytest.mark.asyncio
async def test_paused_status_preserved_on_refresh(monkeypatch):
    refreshes: list[dict] = []

    async def fake_wait(*events, seconds):
        pass

    async def fake_publish(redis_client, **kwargs):
        refreshes.append(kwargs)

    monkeypatch.setattr(m, "_wait_interruptible", fake_wait)
    monkeypatch.setattr(m, "_publish_runtime_status", fake_publish)
    monkeypatch.setattr(m, "RUNTIME_REFRESH_SECONDS", 120)

    ev = asyncio.Event()
    await m._sleep_with_runtime_refresh(None, ev, seconds=300.0, status="paused")

    assert refreshes  # были освежения
    assert all(r.get("status") == "paused" for r in refreshes)
