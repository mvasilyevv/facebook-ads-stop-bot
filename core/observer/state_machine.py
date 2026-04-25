# -*- coding: utf-8 -*-
"""Конечный автомат состояний алертов (FSM).

Переходы:
    NORMAL → WARNING_SENT (если стадия WARNING)
    NORMAL → STOP_SENT    (если стадия STOP)
    WARNING_SENT → STOP_SENT (если стадия STOP — эскалация)
    WARNING_SENT → WARNING_SENT (повторный WARNING — без повторной отправки)
    STOP_SENT → STOP_SENT (повторный STOP — без повторной отправки)
    STOP_SENT → CLAIMED (пользователь нажал «Отключить»)
    CLAIMED → DISABLED (объявление выключено)
"""

from __future__ import annotations

import logging
import uuid

from core.disable_tasks import is_delivery_disabled
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
        return AlertState.WARNING_SENT, token, True

    # Из WARNING — эскалация до STOP отправляется, повторный WARNING — нет
    if previous == AlertState.WARNING_SENT:
        if next_stage == AlertStage.STOP:
            return AlertState.STOP_SENT, token, True
        return AlertState.WARNING_SENT, token, False

    # Из STOP_SENT, CLAIMED, DISABLED — STOP не дублируем, но CLAIMED может откатиться до WARNING.
    if previous == AlertState.STOP_SENT:
        return AlertState.STOP_SENT, token, False

    if previous == AlertState.CLAIMED:
        if next_stage == AlertStage.WARNING:
            return AlertState.WARNING_SENT, token, False
        return AlertState.CLAIMED, token, False

    if previous == AlertState.DISABLED:
        return AlertState.DISABLED, token, False

    logger.warning("FSM: неизвестное состояние %s, сбрасываю в NORMAL", previous)
    return AlertState.NORMAL, None, False


def _state_for_emitted_stage(stage: AlertStage) -> AlertState:
    """Возвращает состояние объявления для отправленного алерта."""
    if stage == AlertStage.STOP:
        return AlertState.CLAIMED
    return AlertState.WARNING_SENT


def reopen_reactivated_alert_state(
    current_state: AlertState | None,
    current_token: str | None,
    delivery_status: str | None,
) -> tuple[AlertState | None, str | None]:
    """Сбрасывает терминальное состояние, если объявление снова начали откручивать."""
    # CLAIMED не сбрасываем по одному только ACTIVE/UNKNOWN:
    # после успешного клика Meta ещё может долго не показывать OFF.
    if current_state == AlertState.DISABLED and not is_delivery_disabled(delivery_status):
        return AlertState.NORMAL, None
    return current_state, current_token


def resolve_off_alert_state(current_state: AlertState) -> AlertState:
    """Определяет итоговое состояние объявления, когда observer увидел реальный OFF."""
    if current_state in (AlertState.CLAIMED, AlertState.DISABLED):
        return AlertState.DISABLED
    return AlertState.NORMAL
