# -*- coding: utf-8 -*-
"""Unit-тесты pure FSM-логики observer."""

from __future__ import annotations

import uuid

import pytest

from core.observer.state_machine import (
    FsmInput,
    decide,
    reset_after_disable_succeeded,
    reset_after_enable_succeeded,
    should_reopen_disabled,
)


def _input(state, *, warning=(), stop=(), stage=None, token=None) -> FsmInput:
    return FsmInput(
        current_state=state,
        current_stage=stage,
        current_open_token=token,
        warning_rule_codes=tuple(warning),
        stop_rule_codes=tuple(stop),
    )


# Сценарий: normal + новый WARNING → warning_sent + emit alert + новый token
def test_normal_to_warning_emits_alert() -> None:
    t = decide(_input("normal", warning=("cpc_high",)))
    assert t.new_state == "warning_sent"
    assert t.new_stage == "warning"
    assert t.emit_alert is True
    assert t.alert_stage == "warning"
    assert t.alert_rule_codes == ("cpc_high",)
    assert t.new_open_token is not None
    assert t.create_disable_task is False


# Сценарий: повторный WARNING после warning_sent — НЕ дублируем emit
def test_warning_sent_does_not_re_emit() -> None:
    tok = uuid.uuid4()
    t = decide(_input("warning_sent", warning=("cpc_high",), stage="warning", token=tok))
    assert t.new_state == "warning_sent"
    assert t.emit_alert is False
    # Сохраняем тот же token (идёт тот же инцидент)
    assert t.new_open_token == tok


# Сценарий: эскалация warning_sent → stop_sent сохраняет тот же open_token
# (incident единый — старые WARNING-кнопки `dis:<fb>:<token>` остаются валидны)
def test_warning_escalates_to_stop() -> None:
    tok = uuid.uuid4()
    t = decide(
        _input(
            "warning_sent",
            warning=("cpc_warn",),
            stop=("spend_no_dep_stop",),
            stage="warning",
            token=tok,
        )
    )
    assert t.new_state == "stop_sent"
    assert t.alert_stage == "stop"
    assert t.emit_alert is True
    assert t.create_disable_task is True
    # Token сохраняется — эскалация = тот же incident
    assert t.new_open_token == tok


# Сценарий: fast-stop из normal сразу в stop_sent (без WARNING шага)
def test_normal_to_stop_directly() -> None:
    t = decide(_input("normal", stop=("spend_no_event",)))
    assert t.new_state == "stop_sent"
    assert t.emit_alert is True
    assert t.alert_stage == "stop"
    assert t.create_disable_task is True


# Сценарий: восстановление warning_sent → normal когда правила перестали срабатывать
def test_warning_recovers_to_normal() -> None:
    tok = uuid.uuid4()
    t = decide(_input("warning_sent", stage="warning", token=tok))
    assert t.new_state == "normal"
    assert t.new_open_token is None
    assert t.emit_alert is False


# Сценарий: восстановление stop_sent → normal без выключения (объявление само исправилось)
def test_stop_recovers_to_normal() -> None:
    tok = uuid.uuid4()
    t = decide(_input("stop_sent", stage="stop", token=tok))
    assert t.new_state == "normal"
    assert t.emit_alert is False
    assert t.create_disable_task is False


# Сценарий: STOP всё ещё активен → алерт не дублируем, но включаем recovery pause-задачи (C1).
# create_disable_task=True позволяет пересоздать задачу, если она не была создана
# (снуз подавил на исходном переходе, либо краш между FSM-коммитом и outbox);
# idempotency_key по open_token защищает от дублей (повтор → UNIQUE conflict → no-op).
def test_stop_sent_still_stop_recovery_disable_task() -> None:
    tok = uuid.uuid4()
    t = decide(_input("stop_sent", stop=("cpc_stop",), stage="stop", token=tok))
    assert t.new_state == "stop_sent"
    assert t.emit_alert is False  # повторный STOP-алерт не шлём
    assert t.create_disable_task is True  # recovery: гарантируем наличие pause-задачи
    assert t.new_open_token == tok  # token инцидента сохранён → idempotency работает


# Сценарий: claimed/disabled — STOP игнорируется (уже в очереди или выключено)
@pytest.mark.parametrize("final_state", ["claimed", "disabled"])
def test_terminal_states_ignored(final_state) -> None:
    tok = uuid.uuid4()
    t = decide(_input(final_state, stop=("cpc_stop",), stage="stop", token=tok))
    assert t.new_state == final_state
    assert t.emit_alert is False
    assert t.create_disable_task is False


# Сценарий: WARNING в claimed/disabled тоже игнорируется
@pytest.mark.parametrize("final_state", ["claimed", "disabled"])
def test_warning_in_terminal_states_ignored(final_state) -> None:
    t = decide(_input(final_state, warning=("cpc",)))
    assert t.new_state == final_state
    assert t.emit_alert is False


# Сценарий: деэскалация stop → warning без emit (странный кейс — был STOP, теперь только WARNING)
def test_stop_deescalates_to_warning_no_emit() -> None:
    tok = uuid.uuid4()
    t = decide(_input("stop_sent", warning=("cpc_warn",), stage="stop", token=tok))
    assert t.new_state == "warning_sent"
    assert t.emit_alert is False  # не дублируем, инцидент тот же
    assert t.new_open_token == tok


# Сценарий: helpers для disable/enable workers
def test_disable_resets_to_disabled() -> None:
    assert reset_after_disable_succeeded("stop_sent") == "disabled"
    assert reset_after_disable_succeeded("claimed") == "disabled"
    assert reset_after_disable_succeeded("normal") == "disabled"


def test_enable_resets_to_normal() -> None:
    assert reset_after_enable_succeeded("disabled") == "normal"
    assert reset_after_enable_succeeded("stop_sent") == "normal"


# Сценарий: pure-функция — одинаковый вход → одинаковый выход (кроме UUID)
def test_decide_is_pure_for_no_emit_cases() -> None:
    """Если переход не создаёт нового UUID, decide должен быть идемпотентен."""
    tok = uuid.uuid4()
    inp = _input("warning_sent", warning=("c1",), stage="warning", token=tok)
    t1 = decide(inp)
    t2 = decide(inp)
    assert t1 == t2  # одинаковый результат, никакого скрытого state


# H3: should_reopen_disabled — реактивированный disabled-ад снова ACTIVE в кабинете.
@pytest.mark.parametrize(
    "state,delivery,expected",
    [
        ("disabled", "ACTIVE", True),  # am-канал
        ("disabled", "Active", True),  # meta-канал (case-insensitive)
        ("disabled", " active ", True),  # с пробелами
        ("disabled", "PAUSED", False),
        ("disabled", "UNKNOWN", False),
        ("disabled", None, False),
        ("disabled", "", False),
        ("normal", "ACTIVE", False),  # не disabled — reopen не нужен
        ("stop_sent", "ACTIVE", False),
        ("claimed", "ACTIVE", False),
        ("warning_sent", "ACTIVE", False),
    ],
)
def test_should_reopen_disabled(state, delivery, expected):
    """reopen-кандидат только для disabled с ACTIVE delivery (любой регистр)."""
    assert should_reopen_disabled(state, delivery) is expected
