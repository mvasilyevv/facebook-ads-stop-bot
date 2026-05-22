# -*- coding: utf-8 -*-
"""Эскалатор переподключения к browser-agent после BROWSER_LOST.

Backoff: 5 → 10 → 20 → 30 → 30 → … (cap).
На 5-й попытке ставится should_send_alert (один раз).
Счётчик сбрасывается через reset() при первом успешном цикле без BROWSER_LOST.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryStep:
    attempt: int
    sleep_seconds: int
    should_send_alert: bool


class BrowserRecoveryEscalator:
    """Состояние повторных попыток переподключения к browser-agent."""

    _BACKOFF = [5, 10, 20, 30]
    _CAP = 30
    ALERT_AFTER_ATTEMPTS = 5

    def __init__(self) -> None:
        self._attempt = 0

    def next_step(self) -> RecoveryStep:
        self._attempt += 1
        if self._attempt <= len(self._BACKOFF):
            sleep = self._BACKOFF[self._attempt - 1]
        else:
            sleep = self._CAP
        return RecoveryStep(
            attempt=self._attempt,
            sleep_seconds=sleep,
            should_send_alert=(self._attempt == self.ALERT_AFTER_ATTEMPTS),
        )

    def reset(self) -> None:
        self._attempt = 0

    @property
    def current_attempt(self) -> int:
        return self._attempt
