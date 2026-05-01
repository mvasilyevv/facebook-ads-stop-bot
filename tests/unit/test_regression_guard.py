# -*- coding: utf-8 -*-
"""Unit-тесты для RegressionGuard."""

from decimal import Decimal
from types import SimpleNamespace

from core.observer.regression_guard import RegressionGuard


def _snap(spend=Decimal("10.00"), clicks=10, leads=2):
    """Вспомогательный снэпшот со стандартными ненулевыми накопительными метриками."""
    return SimpleNamespace(
        spend=spend,
        clicks=clicks,
        leads=leads,
        registrations=0,
        deposits=0,
        outbound_clicks=0,
        landing_page_views=0,
    )


def _new_data(spend=Decimal("0.01"), clicks=0, leads=0):
    """Вспомогательный словарь с уменьшенными метриками (регрессия)."""
    return {
        "spend": spend,
        "clicks": clicks,
        "leads": leads,
        "registrations": 0,
        "deposits": 0,
        "outbound_clicks": 0,
        "landing_page_views": 0,
    }


# Сценарий 1: первый раз new<old → блокировано, счётчик=1.
def test_first_regression_is_blocked():
    guard = RegressionGuard()
    old = _snap()
    blocked = guard.should_block("ad-1", old, _new_data())
    assert blocked is True
    assert guard._counters.get("ad-1") == 1


# Сценарий 2: второй раз подряд new<old → блокировано, счётчик=2.
def test_second_consecutive_regression_still_blocked():
    guard = RegressionGuard()
    old = _snap()
    guard.should_block("ad-1", old, _new_data())
    blocked = guard.should_block("ad-1", old, _new_data())
    assert blocked is True
    assert guard._counters.get("ad-1") == 2


# Сценарий 3: третий раз подряд new<old → ПРИНЯТО, счётчик сброшен.
def test_third_consecutive_regression_is_accepted():
    guard = RegressionGuard()
    old = _snap()
    guard.should_block("ad-1", old, _new_data())
    guard.should_block("ad-1", old, _new_data())
    blocked = guard.should_block("ad-1", old, _new_data())
    assert blocked is False
    assert guard._counters.get("ad-1") == 0


# Сценарий 3 дополнение: после принятия pop_force_accepted возвращает ad_id.
def test_force_accepted_set_populated_on_third_regression():
    guard = RegressionGuard()
    old = _snap()
    guard.should_block("ad-1", old, _new_data())
    guard.should_block("ad-1", old, _new_data())
    guard.should_block("ad-1", old, _new_data())
    accepted = guard.pop_force_accepted()
    assert "ad-1" in accepted
    # После pop сет должен быть пуст
    assert len(guard.pop_force_accepted()) == 0


# Сценарий 4: после блокировки приходит new>=old → счётчик сброшен, обычная запись.
def test_recovery_after_regression_resets_counter():
    guard = RegressionGuard()
    old = _snap()
    # Два подряд регрессии
    guard.should_block("ad-1", old, _new_data())
    guard.should_block("ad-1", old, _new_data())
    # Нормальные данные — больше или равно
    good_data = {
        "spend": Decimal("15.00"),
        "clicks": 12,
        "leads": 3,
        "registrations": 0,
        "deposits": 0,
        "outbound_clicks": 0,
        "landing_page_views": 0,
    }
    blocked = guard.should_block("ad-1", old, good_data)
    assert blocked is False
    assert "ad-1" not in guard._counters


# Сценарий 5: разные ad_id ведут счётчики независимо.
def test_independent_counters_per_ad_id():
    guard = RegressionGuard()
    old = _snap()
    # Два раза для ad-A, один раз для ad-B
    guard.should_block("ad-A", old, _new_data())
    guard.should_block("ad-A", old, _new_data())
    guard.should_block("ad-B", old, _new_data())
    assert guard._counters.get("ad-A") == 2
    assert guard._counters.get("ad-B") == 1
    # Третий раз для ad-A — принято
    blocked_a = guard.should_block("ad-A", old, _new_data())
    # Второй раз для ad-B — ещё заблокировано
    blocked_b = guard.should_block("ad-B", old, _new_data())
    assert blocked_a is False
    assert blocked_b is True
    assert guard._counters.get("ad-A") == 0
    assert guard._counters.get("ad-B") == 2


# Проверяем, что old_snap=None не вызывает ошибок и не блокирует.
def test_none_old_snap_not_blocked():
    guard = RegressionGuard()
    blocked = guard.should_block("ad-1", None, _new_data())
    assert blocked is False
