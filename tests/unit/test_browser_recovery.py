# -*- coding: utf-8 -*-
"""Проверяет экспоненциальный backoff 5→10→20→30→30… и TG-алерт на 5-й попытке."""

from core.observer.browser_recovery import BrowserRecoveryEscalator


def test_first_attempt_sleeps_5s():
    """Первая попытка — sleep 5с, attempt=1, без алерта."""
    esc = BrowserRecoveryEscalator()
    step = esc.next_step()
    assert step.sleep_seconds == 5
    assert step.attempt == 1
    assert not step.should_send_alert


def test_backoff_progression():
    """Backoff: 5, 10, 20, 30, 30, 30…"""
    esc = BrowserRecoveryEscalator()
    sleeps = [esc.next_step().sleep_seconds for _ in range(6)]
    assert sleeps == [5, 10, 20, 30, 30, 30]


def test_alert_on_fifth_attempt():
    """На 5-й попытке should_send_alert=True, на 4-й — False."""
    esc = BrowserRecoveryEscalator()
    steps = [esc.next_step() for _ in range(5)]
    assert not steps[3].should_send_alert
    assert steps[4].should_send_alert


def test_reset():
    """reset() обнуляет счётчик."""
    esc = BrowserRecoveryEscalator()
    esc.next_step()
    esc.next_step()
    esc.reset()
    assert esc.next_step().attempt == 1
