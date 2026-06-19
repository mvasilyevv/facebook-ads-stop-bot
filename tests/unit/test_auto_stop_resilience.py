# -*- coding: utf-8 -*-
"""Unit-тесты money-resilience авто-стопа: pause_ad создаётся с повышенным
лимитом ретраев, чтобы пережить ~1 час сетевого outage graph.facebook.com.

Дефолтных 5 попыток (~7.5 мин по backoff 30/60/120/240/300s-cap) мало — money-стоп
умирал, а объявление продолжало крутить убыток. N=15 даёт ~62 мин (1 час):
3 экспоненциальных интервала (60+120+240с) + 11×300с = 3720с.
Почему не 6ч: health_watchdog probe детектирует мёртвый канал + шлёт CRITICAL в TG;
длинный retry только маскировал корень. См. _AUTO_STOP_MAX_ATTEMPTS.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

import core.meta_api.queue as mq
from core.observer import writers
from core.observer.state_machine import FsmTransition


def _stop_transition() -> FsmTransition:
    """FSM-решение со стоп-задачей (normal → stop_sent, create_disable_task=True)."""
    return FsmTransition(
        new_state="stop_sent",
        new_stage=None,
        new_open_token=uuid.uuid4(),
        create_disable_task=True,
    )


# Авто-стоп создаёт pause_ad с _AUTO_STOP_MAX_ATTEMPTS (не дефолтные 5) — переживает outage
@pytest.mark.asyncio
async def test_auto_stop_uses_bumped_max_attempts(monkeypatch) -> None:
    spy = AsyncMock(return_value=777)
    # writers импортирует create_mutation_task внутри функции из core.meta_api.queue —
    # патчим источник, а не атрибут writers.
    monkeypatch.setattr(mq, "create_mutation_task", spy)

    task_id = await writers.maybe_create_disable_task(
        engine=object(),
        transition=_stop_transition(),
        fb_ad_id="230011223344",
        open_token=None,
    )

    assert task_id == 777
    spy.assert_awaited_once()
    kwargs = spy.await_args.kwargs
    assert kwargs["requested_by"] == "bot_auto_stop"
    assert kwargs["status"] == "pending"
    # Ключевой инвариант money-safety: лимит ретраев поднят над дефолтом 5.
    assert kwargs["max_attempts"] == writers._AUTO_STOP_MAX_ATTEMPTS
    assert writers._AUTO_STOP_MAX_ATTEMPTS > 5


# Покрытие по времени ~1ч: 14 интервалов (60+120+240 + 11×300 = 3720с ≈ 62 мин).
# Константа должна быть ровно 15 — владелец осознанно снизил с 72 (~6ч) до ~1ч:
# health_watchdog probe детектирует мёртвый канал, длинный retry маскировал корень.
def test_auto_stop_max_attempts_covers_one_hour() -> None:
    n = writers._AUTO_STOP_MAX_ATTEMPTS
    assert n == 15, f"Ожидается 15 (~1ч), получено {n}; менять только осознанно"
    # Суммарная задержка: _calc_next_retry(i+1) для i=0..n-2
    # = min(30*2^1,300) + ... + min(30*2^(n-1),300)
    total_wait = sum(min(30 * (2 ** (i + 1)), 300) for i in range(n - 1))
    assert 3600 <= total_wait <= 4200, (
        f"Суммарное окно ретраев {total_wait}с должно быть ~1ч (3600-4200с), "
        f"не 6ч и не менее 60 мин"
    )


# Без стоп-решения (create_disable_task=False) задача не создаётся вовсе
@pytest.mark.asyncio
async def test_no_task_when_transition_has_no_disable(monkeypatch) -> None:
    spy = AsyncMock(return_value=1)
    monkeypatch.setattr(mq, "create_mutation_task", spy)

    transition = FsmTransition(
        new_state="warning_sent",
        new_stage=None,
        new_open_token=uuid.uuid4(),
        create_disable_task=False,
    )
    task_id = await writers.maybe_create_disable_task(
        engine=object(),
        transition=transition,
        fb_ad_id="230011223344",
        open_token=None,
    )

    assert task_id is None
    spy.assert_not_awaited()
