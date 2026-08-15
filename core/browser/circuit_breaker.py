# -*- coding: utf-8 -*-
"""Async circuit-breaker для внешних сервисов (browser-agent, Vision).

Паттерн: CLOSED → OPEN (при N фейлах подряд) → HALF_OPEN (после таймаута восстановления)
→ CLOSED (если пробный запрос успешен) / OPEN (если снова фейл).

Потокобезопасен в рамках одного event loop через asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from enum import Enum, auto
from typing import TypeVar

from core.metrics import record_vision_failure
from core.safe_diagnostics import safe_exception_diagnostic

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class CircuitState(Enum):
    """Возможные состояния circuit-breaker."""

    CLOSED = auto()  # Нормальная работа — запросы проходят
    OPEN = auto()  # Сервис недоступен — запросы сразу отклоняются
    HALF_OPEN = auto()  # Пробный запрос — проверяем восстановление


class CircuitOpenError(RuntimeError):
    """Circuit-breaker открыт — сервис считается недоступным.

    Поднимается вместо реального запроса, чтобы избежать ожидания таймаута.
    """

    def __init__(self, name: str, retry_after_seconds: float) -> None:
        self.name = name
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Circuit-breaker «{name}» открыт. Повтор возможен через {retry_after_seconds:.0f} сек."
        )


class AsyncCircuitBreaker:
    """Async circuit-breaker для защиты от каскадных сбоев.

    Args:
        name: Имя для логов (например, "browser-agent").
        failure_threshold: Сколько фейлов подряд переводят в OPEN (по умолчанию 3).
        recovery_timeout: Секунд в состоянии OPEN перед переходом в HALF_OPEN (по умолчанию 60).
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._opened_at: float | None = None  # monotonic timestamp перехода в OPEN
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Текущее состояние (без блокировки — только для диагностики)."""
        return self._state

    def get_state(self) -> dict:
        """Информация о текущем состоянии для диагностики/health-check."""
        retry_after: float | None = None
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            elapsed = time.monotonic() - self._opened_at
            retry_after = max(0.0, self.recovery_timeout - elapsed)
        return {
            "name": self.name,
            "state": self._state.name,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "retry_after_seconds": retry_after,
        }

    async def check_open(self) -> None:
        """Проверить состояние circuit-breaker. Поднимает CircuitOpenError если OPEN.

        Не записывает фейл/успех — только проверка. Используется перед streaming-вызовами.
        """
        async with self._lock:
            await self._maybe_transition_to_half_open()
            if self._state == CircuitState.OPEN:
                elapsed = (
                    (time.monotonic() - self._opened_at) if self._opened_at is not None else 0.0
                )
                retry_after = max(0.0, self.recovery_timeout - elapsed)
                raise CircuitOpenError(self.name, retry_after)

    async def record_failure(self, exc: Exception) -> None:
        """Явно зафиксировать фейл (для streaming-вызовов, где нельзя использовать call).

        Потокобезопасен — использует внутренний lock.
        """
        async with self._lock:
            await self._record_failure(exc)

    async def record_success(self) -> None:
        """Явно зафиксировать успех (для streaming-вызовов, где нельзя использовать call).

        Потокобезопасен — использует внутренний lock.
        """
        async with self._lock:
            await self._record_success()

    async def call(
        self,
        func: Callable[..., Awaitable[_T]],
        *args,
        **kwargs,
    ) -> _T:
        """Выполнить async-функцию через circuit-breaker.

        В состоянии OPEN сразу поднимает CircuitOpenError (без ожидания).
        В состоянии HALF_OPEN пускает один пробный запрос.
        В состоянии CLOSED работает как обычный await.

        Args:
            func: Async-функция для вызова.
            *args: Позиционные аргументы для func.
            **kwargs: Именованные аргументы для func.

        Raises:
            CircuitOpenError: Если circuit-breaker открыт.
            Exception: Любое исключение из func пробрасывается выше после записи фейла.
        """
        async with self._lock:
            await self._maybe_transition_to_half_open()
            if self._state == CircuitState.OPEN:
                elapsed = (
                    (time.monotonic() - self._opened_at) if self._opened_at is not None else 0.0
                )
                retry_after = max(0.0, self.recovery_timeout - elapsed)
                raise CircuitOpenError(self.name, retry_after)

        # Выполняем запрос вне блокировки, чтобы не блокировать другие корутины
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            async with self._lock:
                await self._record_failure(exc)
            raise
        else:
            async with self._lock:
                await self._record_success()
            return result

    async def _maybe_transition_to_half_open(self) -> None:
        """Переход OPEN → HALF_OPEN по истечении recovery_timeout. Вызывать под блокировкой."""
        if self._state != CircuitState.OPEN:
            return
        if self._opened_at is None:
            return
        elapsed = time.monotonic() - self._opened_at
        if elapsed >= self.recovery_timeout:
            self._state = CircuitState.HALF_OPEN
            logger.info(
                "Circuit-breaker «%s»: OPEN → HALF_OPEN (прошло %.0f сек, порог %.0f сек). "
                "Выполняется пробный запрос.",
                self.name,
                elapsed,
                self.recovery_timeout,
            )

    async def _record_success(self) -> None:
        """Зафиксировать успешный вызов. Вызывать под блокировкой."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info(
                "Circuit-breaker «%s»: HALF_OPEN → CLOSED (пробный запрос успешен).",
                self.name,
            )
        elif self._state == CircuitState.CLOSED and self._failure_count > 0:
            logger.debug(
                "Circuit-breaker «%s»: сброс счётчика фейлов (было %d).",
                self.name,
                self._failure_count,
            )
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = None

    async def _record_failure(self, exc: Exception) -> None:
        """Зафиксировать неудачный вызов. Вызывать под блокировкой."""
        if self._state == CircuitState.HALF_OPEN:
            # Пробный запрос провалился — сразу обратно в OPEN
            self._opened_at = time.monotonic()
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit-breaker «%s»: HALF_OPEN → OPEN (пробный запрос провалился: %s).",
                self.name,
                safe_exception_diagnostic(exc),
            )
            return

        # CLOSED — инкрементируем счётчик
        self._failure_count += 1
        logger.debug(
            "Circuit-breaker «%s»: фейл %d/%d (%s).",
            self.name,
            self._failure_count,
            self.failure_threshold,
            safe_exception_diagnostic(exc),
        )
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit-breaker «%s»: CLOSED → OPEN после %d фейлов подряд. "
                "Запросы будут отклоняться на %.0f сек. Последняя ошибка: %s",
                self.name,
                self._failure_count,
                self.recovery_timeout,
                safe_exception_diagnostic(exc),
            )
            # Фиксируем переход в OPEN как фейл Vision API
            record_vision_failure()
