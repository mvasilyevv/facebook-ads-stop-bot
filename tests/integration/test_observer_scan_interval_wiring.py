# -*- coding: utf-8 -*-
"""Темп скана определяется угрозой прошедшего цикла, а не одной константой (#251).

``core/observer/adaptive_interval.py`` покрыт как чистая арифметика, но его
подключение к рабочему циклу не было закреплено ничем: интеграционные тесты
``main_loop`` подменяли ``_wait_for_durable_scan`` заглушкой, которая
игнорировала аргументы, — а значит, всю цепочку
``summary → resolve_scan_mode → compute_adaptive_interval → compute_remaining_sleep``
можно было выкинуть из цикла и весь набор оставался зелёным. Тогда объявление
за порогом снова сканировалось бы в спокойном темпе (инцидент 02.07).

Здесь наблюдается решение о планировании — сколько цикл реально ждёт до
следующего скана, — а не текст исходника.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import text

import apps.observer_worker.main as obs_main
from core.observer.adaptive_interval import JITTER_FRACTION

# База выбрана заметно выше MIN_INTERVAL_SECONDS=10: иначе clamp подтянул бы
# все режимы к одному числу и разница между ними стала бы ненаблюдаемой.
_BASE_INTERVAL_SECONDS = 200.0


@pytest_asyncio.fixture
async def observer_base_interval(pg_engine):
    """Скан включён, база = CALM-интервал оператора."""
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'observer_scan'"))
        await conn.execute(
            text(
                """
                INSERT INTO observer_config
                    (singleton_key, is_scanning_enabled, interval_seconds, campaign_ids)
                VALUES ('default', TRUE, :interval, ARRAY['1001'])
                ON CONFLICT (singleton_key) DO UPDATE
                SET is_scanning_enabled = TRUE,
                    interval_seconds = EXCLUDED.interval_seconds,
                    campaign_ids = EXCLUDED.campaign_ids
                """
            ),
            {"interval": int(_BASE_INTERVAL_SECONDS)},
        )
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'observer_scan'"))
        await conn.execute(
            text(
                "UPDATE observer_config SET interval_seconds = 90, "
                "campaign_ids = ARRAY[]::text[] WHERE singleton_key = 'default'"
            )
        )


def _summary(**overrides) -> dict:
    summary = {
        "outcome": "success",
        "accounts": [{"ad_account_id": "111"}],
        "alerts_stop": 0,
        "alerts_warning": 0,
        "rows_with_offer": 1,
        "ads_in_stop_state": 0,
        "ads_in_warning_state": 0,
    }
    summary.update(overrides)
    return summary


async def _one_cycle_wait(monkeypatch, *, summary: dict, cycle_seconds: float = 0.0) -> float:
    """Прогоняет ровно один цикл observer'а и возвращает запрошенную им паузу."""
    waits: list[float] = []

    async def _capture_wait(_engine, _shutdown, *, worker_id, seconds):  # noqa: ARG001
        waits.append(float(seconds))
        return None

    async def _scan(_engine, *, task, gate):  # noqa: ARG001
        if cycle_seconds:
            await asyncio.sleep(cycle_seconds)
        return summary

    monkeypatch.setattr(obs_main, "_wait_for_durable_scan", _capture_wait)
    monkeypatch.setattr(obs_main, "_run_claimed_observer_scan", _scan)

    cycles = {"n": 0}

    def _should_continue() -> bool:
        cycles["n"] += 1
        return cycles["n"] <= 1

    async def _gate_factory():
        return object()

    await obs_main.main_loop(gate_factory=_gate_factory, should_continue=_should_continue)

    assert waits, "цикл не дошёл до планирования следующего скана"
    return waits[0]


@pytest.mark.asyncio
@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    ("summary", "multiplier"),
    [
        pytest.param(_summary(alerts_stop=1), 0.2, id="stop-hit"),
        pytest.param(_summary(alerts_warning=1), 0.5, id="warning-hit"),
        pytest.param(_summary(ads_in_stop_state=1), 0.2, id="standing-stop-incident"),
        pytest.param(_summary(), 1.0, id="calm"),
        pytest.param(_summary(rows_with_offer=0), 1.5, id="idle"),
    ],
)
async def test_next_scan_is_scheduled_by_the_threat_of_the_cycle_just_finished(
    pg_engine,
    observer_base_interval,
    monkeypatch,
    summary: dict,
    multiplier: float,
) -> None:
    """Цикл со стопом ждёт кратно меньше спокойного, а холостой — дольше."""
    waited = await _one_cycle_wait(monkeypatch, summary=summary)

    target = _BASE_INTERVAL_SECONDS * multiplier
    # Границы — только объявленный джиттер (±10%) и время самого цикла.
    assert target * (1 - JITTER_FRACTION) - 5.0 <= waited <= target * (1 + JITTER_FRACTION)


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_period_counts_from_the_start_of_the_cycle_not_from_its_end(
    pg_engine,
    observer_base_interval,
    monkeypatch,
) -> None:
    """Долгий скан съедает период, а не добавляется к нему сверху.

    Иначе свежесть снимка тихо уезжает на длительность обхода: период между
    началами циклов становится «база + скан», и слайдер оператора перестаёт
    означать то, что на нём написано.

    База здесь занижена намеренно: длительность цикла обязана перекрывать всю
    полосу джиттера, иначе разница «вычли / не вычли» тонет в случайности.
    """
    short_base = 20.0
    cycle_seconds = 6.0
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE observer_config SET interval_seconds = :i WHERE singleton_key = 'default'"
            ),
            {"i": int(short_base)},
        )

    waited = await _one_cycle_wait(
        monkeypatch,
        summary=_summary(),
        cycle_seconds=cycle_seconds,
    )

    # Верх полосы джиттера минус длительность цикла: без вычитания цикла
    # минимально возможная пауза (нижняя граница джиттера) уже выше этой планки.
    assert waited <= short_base * (1 + JITTER_FRACTION) - cycle_seconds
    # И это всё-таки пауза, а не мгновенный повтор.
    assert waited >= short_base * (1 - JITTER_FRACTION) - cycle_seconds - 1.0
