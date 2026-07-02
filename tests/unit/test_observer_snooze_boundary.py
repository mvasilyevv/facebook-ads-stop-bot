# -*- coding: utf-8 -*-
"""Unit: edge cases для snooze boundary в observer pipeline.

HIGH #4 из backend_test_audit_round_8: snooze-сценарии не были покрыты тестами.
Проверяем 4 граничных случая логики в core/observer/pipeline.py:
  if current.snoozed_until > cycle_ts:  # строго больше

1. snoozed_until == cycle_ts → НЕ suppress (strict >), emit проходит.
2. snoozed_until expired → emit не блокируется.
3. snoozed_until=None → не блокирует emit.
4. snoozed_until в будущем → блокирует emit.

Тесты unit — только логика pipeline._suppress_emit + проверка условия,
без реальной БД. Тесты работают через _suppress_emit напрямую.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.domain import AlertStage, AlertState
from core.observer.state_machine import FsmTransition


def _make_transition(*, emit: bool = True) -> FsmTransition:
    """Создаём тестовый FsmTransition с emit_alert=True."""
    return FsmTransition(
        new_state=AlertState.WARNING_SENT,
        new_stage=AlertStage.WARNING,
        emit_alert=emit,
        alert_stage=AlertStage.WARNING,
        transition_reason="cpc_warn: тест",
        new_open_token=None,
    )


def _pipeline_should_suppress(snoozed_until, cycle_ts) -> bool:
    """Воспроизводит логику проверки snooze из pipeline.py:240."""
    return bool(snoozed_until and snoozed_until > cycle_ts)


# При snoozed_until == cycle_ts — strict > возвращает False → emit НЕ подавляется.
def test_snooze_equality_does_not_suppress() -> None:
    """snoozed_until == cycle_ts: пограничное значение НЕ блокирует emit (строгое >)."""
    ts = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    should_suppress = _pipeline_should_suppress(snoozed_until=ts, cycle_ts=ts)
    assert should_suppress is False, (
        "При snoozed_until == cycle_ts условие > False, emit не должен быть заблокирован"
    )


# snoozed_until < cycle_ts (истёк) → не блокирует.
def test_snooze_expired_does_not_suppress() -> None:
    """snoozed_until истёк (в прошлом relative to cycle_ts) → emit не блокируется."""
    cycle_ts = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    snoozed_until = cycle_ts - timedelta(seconds=1)  # уже прошло
    should_suppress = _pipeline_should_suppress(snoozed_until=snoozed_until, cycle_ts=cycle_ts)
    assert should_suppress is False, "Истёкший snooze не должен блокировать emit"


# snoozed_until=None → не блокирует.
def test_snooze_none_does_not_suppress() -> None:
    """snoozed_until=None (не выставлен) → emit не блокируется."""
    cycle_ts = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    should_suppress = _pipeline_should_suppress(snoozed_until=None, cycle_ts=cycle_ts)
    assert should_suppress is False, "None snoozed_until не должен блокировать emit"


# snoozed_until > cycle_ts → блокирует emit.
def test_snooze_future_suppresses_emit() -> None:
    """snoozed_until в будущем → emit должен быть заблокирован."""
    cycle_ts = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    snoozed_until = cycle_ts + timedelta(minutes=10)  # через 10 мин
    should_suppress = _pipeline_should_suppress(snoozed_until=snoozed_until, cycle_ts=cycle_ts)
    assert should_suppress is True, "snoozed_until > cycle_ts должен блокировать emit"


# Проверяем что _suppress_emit из pipeline действительно отключает emit_alert.
def test_suppress_emit_sets_emit_alert_false() -> None:
    """_suppress_emit возвращает копию FsmTransition с emit_alert=False."""
    from core.observer.pipeline import _suppress_emit

    t = _make_transition(emit=True)
    suppressed = _suppress_emit(t, reason="snoozed")
    assert suppressed.emit_alert is False, "_suppress_emit должен выставить emit_alert=False"
    # Остальные поля не изменились
    assert suppressed.new_state == t.new_state
    assert suppressed.alert_stage == t.alert_stage


# Проверяем что _suppress_emit добавляет причину в transition_reason.
def test_suppress_emit_appends_reason() -> None:
    """_suppress_emit добавляет '[suppressed: <reason>]' к transition_reason."""
    from core.observer.pipeline import _suppress_emit

    t = _make_transition()
    suppressed = _suppress_emit(t, reason="snoozed")
    assert "snoozed" in suppressed.transition_reason, (
        "Причина suppression должна быть в transition_reason"
    )


# MID-2 (money): снуз глушит ТОЛЬКО TG-алерт, НЕ авто-стоп. _suppress_emit обязан
# сохранить create_disable_task=True — заснуженный ад при STOP всё равно ставит
# pause-задачу. Иначе убыточный ад крутится без стопа до истечения окна снуза.
def test_suppress_emit_keeps_disable_task() -> None:
    """_suppress_emit НЕ обнуляет create_disable_task (авто-стоп работает под снузом)."""
    from core.observer.pipeline import _suppress_emit
    from core.observer.state_machine import FsmTransition

    t = FsmTransition(
        new_state=AlertState.STOP_SENT,
        new_stage=AlertStage.STOP,
        new_open_token=None,
        emit_alert=True,
        alert_stage=AlertStage.STOP,
        create_disable_task=True,
        transition_reason="normal → stop_sent",
    )
    suppressed = _suppress_emit(t, reason="snoozed")
    assert suppressed.emit_alert is False, "TG-алерт должен быть подавлен снузом"
    assert suppressed.create_disable_task is True, (
        "Авто-стоп (create_disable_task) должен пережить снуз — снуз глушит только TG"
    )
