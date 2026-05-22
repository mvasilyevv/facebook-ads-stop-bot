# -*- coding: utf-8 -*-
"""Эскалатор попыток восстановления при STALE_DATA.

Лестница:
    Попытка 1: REFRESH,     sleep 15с
    Попытка 2: HARD_RELOAD, sleep 30с
    Попытка 3+: HARD_RELOAD, sleep 60с (cap)
На попытке 5 ставится флаг should_send_alert (один раз).
Счётчик сбрасывается через reset() при первом успешном цикле без STALE_DATA.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StaleAction(Enum):
    REFRESH = "REFRESH"
    HARD_RELOAD = "HARD_RELOAD"


@dataclass(frozen=True)
class StaleEscalationStep:
    kind: StaleAction
    sleep_seconds: int
    attempt: int
    should_send_alert: bool


class StaleDataEscalator:
    """Состояние счётчика попыток восстановления."""

    ALERT_AFTER_ATTEMPTS = 5

    def __init__(self) -> None:
        self._attempt = 0

    def next_action(self) -> StaleEscalationStep:
        self._attempt += 1
        if self._attempt == 1:
            kind = StaleAction.REFRESH
            sleep = 15
        elif self._attempt == 2:
            kind = StaleAction.HARD_RELOAD
            sleep = 30
        else:
            kind = StaleAction.HARD_RELOAD
            sleep = 60
        return StaleEscalationStep(
            kind=kind,
            sleep_seconds=sleep,
            attempt=self._attempt,
            should_send_alert=(self._attempt == self.ALERT_AFTER_ATTEMPTS),
        )

    def reset(self) -> None:
        self._attempt = 0

    @property
    def current_attempt(self) -> int:
        return self._attempt
