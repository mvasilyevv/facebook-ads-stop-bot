# -*- coding: utf-8 -*-
"""Проверяет эскалацию STALE_DATA: refresh → hard reload → TG-алерт."""

from core.observer.stale_data_handler import StaleAction, StaleDataEscalator


def test_first_attempt_is_refresh():
    """Первая попытка — обычный refresh, sleep 15с."""
    esc = StaleDataEscalator()
    action = esc.next_action()
    assert action.kind == StaleAction.REFRESH
    assert action.sleep_seconds == 15
    assert action.attempt == 1
    assert not action.should_send_alert


def test_second_attempt_is_hard_reload():
    """Вторая попытка — hard reload, sleep 30с."""
    esc = StaleDataEscalator()
    esc.next_action()
    action = esc.next_action()
    assert action.kind == StaleAction.HARD_RELOAD
    assert action.sleep_seconds == 30
    assert action.attempt == 2


def test_third_and_more_hard_reload_60s():
    """3+ попытки — hard reload, sleep 60с (cap)."""
    esc = StaleDataEscalator()
    esc.next_action()
    esc.next_action()
    third = esc.next_action()
    fourth = esc.next_action()
    assert third.kind == StaleAction.HARD_RELOAD
    assert third.sleep_seconds == 60
    assert fourth.sleep_seconds == 60


def test_alert_triggered_at_fifth_attempt():
    """На пятой попытке — should_send_alert=True."""
    esc = StaleDataEscalator()
    actions = [esc.next_action() for _ in range(5)]
    assert all(not a.should_send_alert for a in actions[:4])
    assert actions[4].should_send_alert


def test_reset_clears_counter():
    """reset() обнуляет счётчик попыток."""
    esc = StaleDataEscalator()
    esc.next_action()
    esc.next_action()
    esc.reset()
    action = esc.next_action()
    assert action.attempt == 1
    assert action.kind == StaleAction.REFRESH
