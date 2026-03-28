# -*- coding: utf-8 -*-
"""Unit-тесты конечного автомата состояний (FSM)."""

from __future__ import annotations

from core.domain import AlertStage, AlertState
from core.observer.state_machine import resolve_transition


# Из NORMAL + STOP → STOP_SENT, должен отправить
def test_normal_to_stop_sends():
    state, token, emit = resolve_transition(
        current_state=AlertState.NORMAL,
        current_token=None,
        next_stage=AlertStage.STOP,
    )
    assert state == AlertState.STOP_SENT
    assert token is not None
    assert emit is True


# Из NORMAL + WARNING → WARNING_SENT, должен отправить
def test_normal_to_warning_sends():
    state, token, emit = resolve_transition(
        current_state=AlertState.NORMAL,
        current_token=None,
        next_stage=AlertStage.WARNING,
    )
    assert state == AlertState.WARNING_SENT
    assert token is not None
    assert emit is True


# Из NORMAL + EARLY_SIGNAL → EARLY_SIGNAL_SENT, должен отправить
def test_normal_to_early_signal_sends():
    state, token, emit = resolve_transition(
        current_state=AlertState.NORMAL,
        current_token=None,
        next_stage=AlertStage.EARLY_SIGNAL,
    )
    assert state == AlertState.EARLY_SIGNAL_SENT
    assert token is not None
    assert emit is True


# Из NORMAL + None → остаётся NORMAL, не отправляет
def test_normal_stays_normal():
    state, token, emit = resolve_transition(
        current_state=AlertState.NORMAL,
        current_token=None,
        next_stage=None,
    )
    assert state == AlertState.NORMAL
    assert token is None
    assert emit is False


# Из EARLY_SIGNAL_SENT + EARLY_SIGNAL → остаётся, НЕ отправляет повторно
def test_early_signal_repeat_no_send():
    state, token, emit = resolve_transition(
        current_state=AlertState.EARLY_SIGNAL_SENT,
        current_token="existing-token",
        next_stage=AlertStage.EARLY_SIGNAL,
    )
    assert state == AlertState.EARLY_SIGNAL_SENT
    assert token == "existing-token"
    assert emit is False


# Из EARLY_SIGNAL_SENT + WARNING → эскалация до WARNING_SENT, отправляет
def test_early_signal_to_warning_escalates():
    state, token, emit = resolve_transition(
        current_state=AlertState.EARLY_SIGNAL_SENT,
        current_token="existing-token",
        next_stage=AlertStage.WARNING,
    )
    assert state == AlertState.WARNING_SENT
    assert token == "existing-token"
    assert emit is True


# Из EARLY_SIGNAL_SENT + STOP → эскалация до STOP_SENT, отправляет
def test_early_signal_to_stop_escalates():
    state, token, emit = resolve_transition(
        current_state=AlertState.EARLY_SIGNAL_SENT,
        current_token="existing-token",
        next_stage=AlertStage.STOP,
    )
    assert state == AlertState.STOP_SENT
    assert token == "existing-token"
    assert emit is True


# Из WARNING_SENT + STOP → эскалация до STOP_SENT, отправляет
def test_warning_to_stop_escalates():
    state, token, emit = resolve_transition(
        current_state=AlertState.WARNING_SENT,
        current_token="existing-token",
        next_stage=AlertStage.STOP,
    )
    assert state == AlertState.STOP_SENT
    assert emit is True


# Из WARNING_SENT + WARNING → остаётся, НЕ отправляет повторно
def test_warning_repeat_no_send():
    state, token, emit = resolve_transition(
        current_state=AlertState.WARNING_SENT,
        current_token="existing-token",
        next_stage=AlertStage.WARNING,
    )
    assert state == AlertState.WARNING_SENT
    assert emit is False


# Из WARNING_SENT + EARLY_SIGNAL → остаётся WARNING_SENT, без понижения
def test_warning_does_not_downgrade_to_early_signal():
    state, token, emit = resolve_transition(
        current_state=AlertState.WARNING_SENT,
        current_token="existing-token",
        next_stage=AlertStage.EARLY_SIGNAL,
    )
    assert state == AlertState.WARNING_SENT
    assert token == "existing-token"
    assert emit is False


# Из STOP_SENT + STOP → остаётся, НЕ отправляет повторно
def test_stop_repeat_no_send():
    state, token, emit = resolve_transition(
        current_state=AlertState.STOP_SENT,
        current_token="existing-token",
        next_stage=AlertStage.STOP,
    )
    assert state == AlertState.STOP_SENT
    assert emit is False


# Из CLAIMED — ничего не отправляется (уже в работе)
def test_claimed_stays():
    state, token, emit = resolve_transition(
        current_state=AlertState.CLAIMED,
        current_token="existing-token",
        next_stage=AlertStage.STOP,
    )
    assert state == AlertState.CLAIMED
    assert emit is False


# Из DISABLED — ничего не отправляется (уже выключено)
def test_disabled_stays():
    state, token, emit = resolve_transition(
        current_state=AlertState.DISABLED,
        current_token="existing-token",
        next_stage=AlertStage.STOP,
    )
    assert state == AlertState.DISABLED
    assert emit is False


# Токен сохраняется при повторных вызовах
def test_token_preserved():
    _, token1, _ = resolve_transition(
        current_state=AlertState.NORMAL,
        current_token=None,
        next_stage=AlertStage.WARNING,
    )
    assert token1 is not None

    _, token2, _ = resolve_transition(
        current_state=AlertState.WARNING_SENT,
        current_token=token1,
        next_stage=AlertStage.STOP,
    )
    assert token2 == token1
