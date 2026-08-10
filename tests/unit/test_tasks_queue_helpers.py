# -*- coding: utf-8 -*-
"""Unit: _calc_retry_available_at exponential backoff exact values.

HIGH #8 из backend_test_audit_round_8: функция _calc_retry_available_at — чистая функция
без зависимостей, не покрыта тестами. Проверяем точные значения задержки backoff:
  base=30s, max=300s, формула: min(30 * 2^attempt, 300)

1. attempt=0 → +30s
2. attempt=1 → +60s
3. attempt=2 → +120s
4. attempt=3 → +240s
5. attempt=4 → +300s (cap)
6. attempt=10 → +300s (cap стабилен)
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import core.tasks.queue as task_queue
from core.tasks.queue import (
    BROWSER_READY_CLAIM_TASK_TYPES,
    Task,
    _calc_retry_available_at,
    _returned_task_rows,
    _returned_value,
    _row_to_task,
    claim_next_task,
)

# Разрешённое отклонение по времени в мс (потокобезопасность datetime.now())
_TOLERANCE_SECONDS = 2


def _approx_seconds(dt: datetime, *, expected: int) -> None:
    """Проверяем что dt - now() ≈ expected секунд (±допуск)."""
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    assert abs(delta - expected) <= _TOLERANCE_SECONDS, (
        f"Ожидали +{expected}s, получили +{delta:.1f}s"
    )


# attempt=0 → задержка 30 секунд
def test_backoff_attempt_0() -> None:
    """Первая попытка (attempt=0): задержка 30 секунд."""
    result = _calc_retry_available_at(0)
    _approx_seconds(result, expected=30)


# attempt=1 → задержка 60 секунд
def test_backoff_attempt_1() -> None:
    """Вторая попытка (attempt=1): задержка 60 секунд."""
    result = _calc_retry_available_at(1)
    _approx_seconds(result, expected=60)


# attempt=2 → задержка 120 секунд
def test_backoff_attempt_2() -> None:
    """Третья попытка (attempt=2): задержка 120 секунд."""
    result = _calc_retry_available_at(2)
    _approx_seconds(result, expected=120)


# attempt=3 → задержка 240 секунд
def test_backoff_attempt_3() -> None:
    """Четвёртая попытка (attempt=3): задержка 240 секунд."""
    result = _calc_retry_available_at(3)
    _approx_seconds(result, expected=240)


# attempt=4 → задержка 300 секунд (cap)
def test_backoff_attempt_4_hits_cap() -> None:
    """Пятая попытка (attempt=4): 30*16=480s > cap → должен дать ровно 300s."""
    result = _calc_retry_available_at(4)
    _approx_seconds(result, expected=300)


# attempt=10 → задержка остаётся на cap 300s
def test_backoff_attempt_10_stays_at_cap() -> None:
    """Десятая попытка: cap 300s стабилен, не растёт дальше."""
    result = _calc_retry_available_at(10)
    _approx_seconds(result, expected=300)


# Монотонность: каждая следующая попытка >= предыдущей до cap
def test_backoff_is_monotone_until_cap() -> None:
    """Задержка монотонно растёт: attempt=0,1,2,3 — каждая следующая больше или равна."""
    delays = [_calc_retry_available_at(a) for a in range(5)]
    for i in range(len(delays) - 1):
        assert delays[i] <= delays[i + 1], (
            f"Задержка не монотонна: attempt={i} ({delays[i]}) > attempt={i + 1} ({delays[i + 1]})"
        )


# Возвращает timezone-aware datetime
def test_backoff_returns_utc_aware_datetime() -> None:
    """_calc_retry_available_at возвращает timezone-aware datetime в UTC."""
    result = _calc_retry_available_at(0)
    assert result.tzinfo is not None, (
        "_calc_retry_available_at должен вернуть timezone-aware datetime"
    )
    assert result > datetime.now(timezone.utc), "available_at должен быть в будущем"


def test_task_snapshot_is_keyword_only_and_has_no_partial_constructor() -> None:
    parameters = inspect.signature(Task).parameters.values()
    assert parameters
    assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters)
    optional = {
        parameter.name
        for parameter in parameters
        if parameter.default is not inspect.Parameter.empty
    }
    assert optional == {
        "browser_profile_id",
        "browser_readiness_generation",
    }


def test_task_row_mapping_is_mandatory() -> None:
    with pytest.raises(TypeError, match="named columns"):
        _row_to_task((1, "meta_api_mutation"))


def test_terminal_update_requires_returning_rows() -> None:
    with pytest.raises(TypeError, match="UPDATE .. RETURNING"):
        _returned_task_rows(SimpleNamespace(rowcount=1))
    with pytest.raises(TypeError, match="named columns"):
        _returned_value(SimpleNamespace(id=1), "id")


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", sorted(BROWSER_READY_CLAIM_TASK_TYPES))
async def test_generic_claim_fails_closed_for_browser_ready_task_types(
    task_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every generic caller is delegated to the durable scheduling gate."""
    captured: dict[str, object] = {}

    async def gated_claim(engine, **kwargs):
        captured["engine"] = engine
        captured.update(kwargs)
        return "gated"

    monkeypatch.setattr(task_queue, "claim_browser_ready_task", gated_claim)
    engine = object()
    result = await claim_next_task(
        engine,
        task_type=task_type,
        lanes=("money",),
        lease_seconds=71,
    )

    assert result == "gated"
    assert captured == {
        "engine": engine,
        "task_type": task_type,
        "lanes": ("money",),
        "worker_id": None,
        "lease_seconds": 71,
    }
