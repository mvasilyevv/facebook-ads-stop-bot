# -*- coding: utf-8 -*-
"""Unit: _calc_next_retry exponential backoff exact values.

HIGH #8 из backend_test_audit_round_8: функция _calc_next_retry — чистая функция
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

from datetime import datetime, timezone

from core.tasks.queue import _calc_next_retry

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
    result = _calc_next_retry(0)
    _approx_seconds(result, expected=30)


# attempt=1 → задержка 60 секунд
def test_backoff_attempt_1() -> None:
    """Вторая попытка (attempt=1): задержка 60 секунд."""
    result = _calc_next_retry(1)
    _approx_seconds(result, expected=60)


# attempt=2 → задержка 120 секунд
def test_backoff_attempt_2() -> None:
    """Третья попытка (attempt=2): задержка 120 секунд."""
    result = _calc_next_retry(2)
    _approx_seconds(result, expected=120)


# attempt=3 → задержка 240 секунд
def test_backoff_attempt_3() -> None:
    """Четвёртая попытка (attempt=3): задержка 240 секунд."""
    result = _calc_next_retry(3)
    _approx_seconds(result, expected=240)


# attempt=4 → задержка 300 секунд (cap)
def test_backoff_attempt_4_hits_cap() -> None:
    """Пятая попытка (attempt=4): 30*16=480s > cap → должен дать ровно 300s."""
    result = _calc_next_retry(4)
    _approx_seconds(result, expected=300)


# attempt=10 → задержка остаётся на cap 300s
def test_backoff_attempt_10_stays_at_cap() -> None:
    """Десятая попытка: cap 300s стабилен, не растёт дальше."""
    result = _calc_next_retry(10)
    _approx_seconds(result, expected=300)


# Монотонность: каждая следующая попытка >= предыдущей до cap
def test_backoff_is_monotone_until_cap() -> None:
    """Задержка монотонно растёт: attempt=0,1,2,3 — каждая следующая больше или равна."""
    delays = [_calc_next_retry(a) for a in range(5)]
    for i in range(len(delays) - 1):
        assert delays[i] <= delays[i + 1], (
            f"Задержка не монотонна: attempt={i} ({delays[i]}) > attempt={i + 1} ({delays[i + 1]})"
        )


# Возвращает timezone-aware datetime
def test_backoff_returns_utc_aware_datetime() -> None:
    """_calc_next_retry возвращает timezone-aware datetime в UTC."""
    result = _calc_next_retry(0)
    assert result.tzinfo is not None, "_calc_next_retry должен вернуть timezone-aware datetime"
    assert result > datetime.now(timezone.utc), "next_retry_at должен быть в будущем"
