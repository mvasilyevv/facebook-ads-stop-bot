# -*- coding: utf-8 -*-
"""Состояние health-алертов: cooldown и трекер активных инцидентов."""

from __future__ import annotations

import time


class AlertCooldown:
    """Хранит время последней отправки алерта по ключу и проверяет cooldown."""

    def __init__(self, cooldown_seconds: float) -> None:
        self._cooldown_seconds = cooldown_seconds
        self._last_sent: dict[str, float] = {}

    def can_send(self, key: str) -> bool:
        last = self._last_sent.get(key, 0.0)
        return (time.monotonic() - last) >= self._cooldown_seconds

    def mark_sent(self, key: str) -> None:
        self._last_sent[key] = time.monotonic()

    def reset(self, key: str) -> None:
        """Сбрасывает cooldown для одного ключа (после авто-resolve)."""
        self._last_sent.pop(key, None)

    def reset_all(self) -> None:
        """Сбрасывает cooldown для всех ключей (например, после wake from sleep)."""
        self._last_sent.clear()


class IncidentTracker:
    """Трекает открытые health-инциденты для отправки auto-resolve."""

    def __init__(self) -> None:
        self._open: dict[str, float] = {}

    def mark_open(self, key: str) -> None:
        if key not in self._open:
            self._open[key] = time.monotonic()

    def resolve(self, key: str) -> bool:
        """Возвращает True, если инцидент был открыт; удаляет ключ из открытых."""
        return self._open.pop(key, None) is not None

    def is_open(self, key: str) -> bool:
        return key in self._open

    def open_keys(self) -> list[str]:
        """Возвращает список ключей открытых инцидентов (копия)."""
        return list(self._open.keys())
