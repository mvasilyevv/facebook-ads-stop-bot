# -*- coding: utf-8 -*-
"""Рабочий цикл трекер-воркера закреплён поведением (#251).

``drain_event_tasks`` и ``reconcile_provider_events`` покрыты каждый в изоляции,
но ``main_loop`` не вызывался ни одним тестом: отметку живости после разбора
очереди и собственное расписание сверки с провайдером можно было удалить, и
весь набор оставался зелёным.

Цикл останавливается штатно — через ``stop_event``, который в тесте выставляет
двойник слушателя пробуждений. Отмены задачи нет: остановка воркера сама по
себе часть проводки.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import apps.tracker_reconciliation_worker.main as tracker


class _LoopRun:
    def __init__(self) -> None:
        self.drain_calls = 0
        self.reconcile_calls = 0
        self.poll_marks: list[bool] = []
        self.drain_raises = False
        self._enough = asyncio.Event()

    def _note_drain(self) -> None:
        self.drain_calls += 1
        if self.drain_calls >= 3:
            self._enough.set()


class _FakeRedis:
    async def aclose(self) -> None:
        return None


class _FakeEngine:
    async def dispose(self) -> None:
        return None


@pytest.fixture
def tracker_loop(monkeypatch: pytest.MonkeyPatch) -> _LoopRun:
    run = _LoopRun()

    async def _drain(_engine):
        run._note_drain()
        if run.drain_raises:
            raise RuntimeError("разбор очереди трекера сорвался")
        return 0

    async def _reconcile(_engine):
        run.reconcile_calls += 1
        return SimpleNamespace(status="ok", accepted=0, drift_before=0, drift_after=0)

    async def _heartbeat(_engine, _name, *, poll_success=False):
        run.poll_marks.append(bool(poll_success))

    async def _metrics_loop(stop, _engine):
        await stop.wait()

    async def _listener(_client, stop_event, wakeup):
        # Двойник слушателя = штатная точка остановки: несколько проходов —
        # и воркеру приходит тот же сигнал, что от SIGTERM.
        await run._enough.wait()
        stop_event.set()
        wakeup.set()

    monkeypatch.setattr(tracker, "_DB_POLL_SECONDS", 0.01)
    monkeypatch.setattr(tracker, "create_async_engine", lambda *_a, **_k: _FakeEngine())
    monkeypatch.setattr(
        tracker,
        "redis_asyncio",
        SimpleNamespace(from_url=lambda *_a, **_k: _FakeRedis()),
    )
    monkeypatch.setattr(tracker, "metrics_loop", _metrics_loop)
    monkeypatch.setattr(tracker, "wakeup_listener", _listener)
    monkeypatch.setattr(tracker, "drain_event_tasks", _drain)
    monkeypatch.setattr(tracker, "reconcile_provider_events", _reconcile)
    monkeypatch.setattr(tracker, "record_worker_heartbeat", _heartbeat)
    return run


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_a_failed_queue_drain_still_proves_the_loop_is_alive(
    tracker_loop: _LoopRun,
) -> None:
    """Сорванный разбор очереди — не зависший воркер, и цикл обязан это показать."""
    tracker_loop.drain_raises = True

    await asyncio.wait_for(tracker.main_loop("postgresql+asyncpg://stub/stub"), timeout=10.0)

    assert tracker_loop.drain_calls >= 3
    assert tracker_loop.poll_marks
    assert all(tracker_loop.poll_marks)


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_provider_reconciliation_keeps_its_own_schedule_not_the_poll_rate(
    tracker_loop: _LoopRun,
) -> None:
    """Сверка с провайдером идёт по своему интервалу, а не на каждом опросе БД.

    Опрос очереди чаще сверки — сознательное разделение: без него каждый
    быстрый проход тянул бы запрос к внешнему провайдеру.
    """
    await asyncio.wait_for(tracker.main_loop("postgresql+asyncpg://stub/stub"), timeout=10.0)

    assert tracker_loop.drain_calls >= 3
    assert tracker_loop.reconcile_calls == 1
