# -*- coding: utf-8 -*-
"""Цикл планировщика сводок не отменяется одним упавшим тиком (#251).

``run_one_tick`` и ``run_pulse_tick`` покрыты каждый в изоляции, но ``tick_loop``
не вызывался ни одним тестом: его проводку — независимость двух тиков и отметку
живости после них — можно было удалить, и весь набор оставался зелёным.

Тик здесь падает намеренно: именно в этом состоянии проводка либо держит, либо
молча тащит за собой второй тик и живость воркера.
"""

from __future__ import annotations

import asyncio

import pytest

import apps.digest_scheduler.main as digest


class _Ticks:
    def __init__(self) -> None:
        self.digest_calls = 0
        self.pulse_calls = 0
        self.poll_marks: list[bool] = []


@pytest.fixture
def failing_digest_tick(monkeypatch: pytest.MonkeyPatch) -> _Ticks:
    ticks = _Ticks()

    async def _digest_tick(*, engine, now, window):  # noqa: ARG001
        ticks.digest_calls += 1
        raise RuntimeError("сводку собрать не удалось")

    async def _pulse_tick(*, engine, now):  # noqa: ARG001
        ticks.pulse_calls += 1
        return "no_slot"

    async def _heartbeat(_engine, _name, *, poll_success=False):
        ticks.poll_marks.append(bool(poll_success))

    monkeypatch.setattr(digest, "CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(digest, "run_one_tick", _digest_tick)
    monkeypatch.setattr(digest, "run_pulse_tick", _pulse_tick)
    monkeypatch.setattr(digest, "record_worker_heartbeat", _heartbeat)
    return ticks


async def _run_briefly(seconds: float = 0.3) -> None:
    """Крутит цикл заданное время и останавливает его штатным стопом."""
    stop = asyncio.Event()
    loop = asyncio.create_task(
        digest.tick_loop(engine=object(), window=digest.DigestWindow(hour=9, minute=0), stop=stop)
    )
    await asyncio.sleep(seconds)
    stop.set()
    await asyncio.wait_for(loop, timeout=5.0)


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_a_failed_digest_tick_does_not_cancel_the_cabinet_pulse(
    failing_digest_tick: _Ticks,
) -> None:
    """Две сводки независимы: сорванная дневная не уносит с собой пульс."""
    await _run_briefly()

    assert failing_digest_tick.digest_calls >= 2
    assert failing_digest_tick.pulse_calls >= 2


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_loop_keeps_reporting_a_successful_poll_after_a_failed_tick(
    failing_digest_tick: _Ticks,
) -> None:
    """Упавший тик — не зависший воркер, и отличать одно от другого обязан цикл."""
    await _run_briefly()

    assert failing_digest_tick.poll_marks
    assert all(failing_digest_tick.poll_marks)
    assert len(failing_digest_tick.poll_marks) >= 2
