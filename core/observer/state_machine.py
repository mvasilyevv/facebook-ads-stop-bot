# -*- coding: utf-8 -*-
"""Конечный автомат состояний алертов (FSM).

Переходы:
    NORMAL → EARLY_SIGNAL_SENT (если стадия EARLY_SIGNAL)
    NORMAL → WARNING_SENT (если стадия WARNING)
    NORMAL → STOP_SENT    (если стадия STOP)
    EARLY_SIGNAL_SENT → EARLY_SIGNAL_SENT (повторный EARLY_SIGNAL — без повторной отправки)
    EARLY_SIGNAL_SENT → WARNING_SENT (эскалация до WARNING)
    EARLY_SIGNAL_SENT → STOP_SENT (эскалация до STOP)
    WARNING_SENT → STOP_SENT (если стадия STOP — эскалация)
    WARNING_SENT → WARNING_SENT (повторный WARNING — без повторной отправки)
    STOP_SENT → STOP_SENT (повторный STOP — без повторной отправки)
    STOP_SENT → CLAIMED (пользователь нажал «Отключить»)
    CLAIMED → DISABLED (объявление выключено)
"""

from __future__ import annotations

import logging
import uuid

from core.domain import AlertStage, AlertState

logger = logging.getLogger(__name__)


def resolve_transition(
    *,
    current_state: AlertState | None,
    current_token: str | None,
    next_stage: AlertStage | None,
) -> tuple[AlertState, str | None, bool]:
    """Определяет следующее состояние и нужно ли отправлять уведомление.

    Возвращает:
        (next_state, open_state_token, should_emit)
    """
    previous = current_state or AlertState.NORMAL

    # Ничего не сработало — возврат в NORMAL
    if next_stage is None:
        return AlertState.NORMAL, None, False

    token = current_token or uuid.uuid4().hex

    # Из NORMAL — любой алерт отправляется
    if previous == AlertState.NORMAL:
        if next_stage == AlertStage.STOP:
            return AlertState.STOP_SENT, token, True
        if next_stage == AlertStage.EARLY_SIGNAL:
            return AlertState.EARLY_SIGNAL_SENT, token, True
        return AlertState.WARNING_SENT, token, True

    # Из EARLY_SIGNAL — повтор не отправляем, но WARNING/STOP эскалируются
    if previous == AlertState.EARLY_SIGNAL_SENT:
        if next_stage == AlertStage.STOP:
            return AlertState.STOP_SENT, token, True
        if next_stage == AlertStage.WARNING:
            return AlertState.WARNING_SENT, token, True
        return AlertState.EARLY_SIGNAL_SENT, token, False

    # Из WARNING — эскалация до STOP отправляется, повторный WARNING — нет
    if previous == AlertState.WARNING_SENT:
        if next_stage == AlertStage.STOP:
            return AlertState.STOP_SENT, token, True
        return AlertState.WARNING_SENT, token, False

    # Из STOP_SENT, CLAIMED, DISABLED — ничего не отправляем (уже обработано)
    if previous == AlertState.STOP_SENT:
        return AlertState.STOP_SENT, token, False

    if previous == AlertState.CLAIMED:
        return AlertState.CLAIMED, token, False

    if previous == AlertState.DISABLED:
        return AlertState.DISABLED, token, False

    logger.warning("FSM: неизвестное состояние %s, сбрасываю в NORMAL", previous)
    return AlertState.NORMAL, None, False
