# -*- coding: utf-8 -*-
"""Pure unit-тесты: contract open_state_token при переходах FSM.

Token = идентификатор «инцидента». Должен сохраняться на протяжении одного
incident'а (warning→stop→claimed→disabled), но генерироваться заново при
открытии нового incident'а из normal или после reopen из disabled.

Это критично для inline-кнопок Telegram: callback_data содержит token; если
observer перетирает token при эскалации, callback `dis:<fb>:<token>` на старой
WARNING-карточке становится невалиден и юзер не сможет отключить ад.
"""

from __future__ import annotations

import uuid

from core.observer.state_machine import FsmInput, decide


def _input(state, *, warning=(), stop=(), stage=None, token=None) -> FsmInput:
    return FsmInput(
        current_state=state,
        current_stage=stage,
        current_open_token=token,
        warning_rule_codes=tuple(warning),
        stop_rule_codes=tuple(stop),
    )


# Сценарий: WARNING_SENT → STOP_SENT — токен ТОТ ЖЕ (эскалация = один incident)
def test_warning_to_stop_keeps_open_token() -> None:
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
    assert t.new_open_token == tok
    assert t.emit_alert is True


# Сценарий: NORMAL → WARNING_SENT — токен новый (открыли incident)
def test_normal_to_warning_generates_new_token() -> None:
    t = decide(_input("normal", warning=("cpc_high",)))
    assert t.new_state == "warning_sent"
    assert t.new_open_token is not None
    # uuid4 не должен совпасть со случайным None-input
    assert isinstance(t.new_open_token, uuid.UUID)


# Сценарий: NORMAL → STOP_SENT (fast-stop) — токен новый (новый incident)
def test_normal_to_stop_generates_new_token() -> None:
    t = decide(_input("normal", stop=("spend_no_event",)))
    assert t.new_state == "stop_sent"
    assert t.new_open_token is not None
    assert isinstance(t.new_open_token, uuid.UUID)


# Сценарий: STOP_SENT → STOP_SENT (всё ещё STOP) — токен сохраняется
def test_stop_repeated_keeps_token() -> None:
    tok = uuid.uuid4()
    t = decide(_input("stop_sent", stop=("cpc_stop",), stage="stop", token=tok))
    assert t.new_state == "stop_sent"
    assert t.new_open_token == tok


# Сценарий: WARNING_SENT → WARNING_SENT (повтор WARNING) — токен сохраняется
def test_warning_repeated_keeps_token() -> None:
    tok = uuid.uuid4()
    t = decide(_input("warning_sent", warning=("cpc",), stage="warning", token=tok))
    assert t.new_state == "warning_sent"
    assert t.new_open_token == tok


# Сценарий: STOP_SENT → WARNING_SENT (деэскалация) — токен сохраняется (тот же incident)
def test_stop_to_warning_keeps_token() -> None:
    tok = uuid.uuid4()
    t = decide(_input("stop_sent", warning=("cpc_warn",), stage="stop", token=tok))
    assert t.new_state == "warning_sent"
    assert t.new_open_token == tok


# Сценарий: WARNING_SENT → NORMAL (восстановление) — токен сбрасывается (incident закрыт)
def test_warning_to_normal_clears_token() -> None:
    tok = uuid.uuid4()
    t = decide(_input("warning_sent", stage="warning", token=tok))
    assert t.new_state == "normal"
    assert t.new_open_token is None


# Сценарий: STOP_SENT → NORMAL (восстановление) — токен сбрасывается
def test_stop_to_normal_clears_token() -> None:
    tok = uuid.uuid4()
    t = decide(_input("stop_sent", stage="stop", token=tok))
    assert t.new_state == "normal"
    assert t.new_open_token is None


# Сценарий: CLAIMED + STOP — состояние не меняется, токен сохраняется
def test_claimed_with_stop_preserves_token() -> None:
    tok = uuid.uuid4()
    t = decide(_input("claimed", stop=("cpc_stop",), stage="stop", token=tok))
    assert t.new_state == "claimed"
    assert t.new_open_token == tok
    assert t.emit_alert is False


# Сценарий: DISABLED + STOP — состояние не меняется, токен сохраняется
def test_disabled_with_stop_preserves_token() -> None:
    tok = uuid.uuid4()
    t = decide(_input("disabled", stop=("cpc_stop",), stage="stop", token=tok))
    assert t.new_state == "disabled"
    assert t.new_open_token == tok


# Сценарий: WARNING_SENT без current_open_token — fallback на новый uuid при эскалации
# (защита от старых записей в БД без token; не должно падать NULL'ом)
def test_warning_to_stop_without_existing_token_generates_new() -> None:
    t = decide(
        _input(
            "warning_sent",
            warning=("cpc_warn",),
            stop=("spend_stop",),
            stage="warning",
            token=None,
        )
    )
    assert t.new_state == "stop_sent"
    assert t.new_open_token is not None
    assert isinstance(t.new_open_token, uuid.UUID)
