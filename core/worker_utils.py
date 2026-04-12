# -*- coding: utf-8 -*-
"""Общие утилиты для воркеров FB_Agent."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import pathlib
import random

logger = logging.getLogger(__name__)


def calculate_retry_delay(attempt: int, base: int = 30, max_delay: int = 300) -> float:
    """Exponential backoff: base * 2^(attempt-1), кап max_delay.

    Args:
        attempt: Номер попытки (начиная с 1).
        base: Базовая задержка в секундах (по умолчанию 30).
        max_delay: Максимальная задержка в секундах (по умолчанию 300).

    Returns:
        Время ожидания в секундах.
    """
    delay = base * (2 ** max(attempt - 1, 0))
    # Jitter ±15% предотвращает thundering herd при одновременных ретраях
    jitter = random.uniform(0.85, 1.15)
    return float(min(delay * jitter, max_delay))


async def wait_for_shutdown_or_timeout(
    shutdown_event: asyncio.Event, timeout_seconds: float
) -> bool:
    """Ждёт shutdown_event или истечения timeout. Возвращает True если shutdown.

    Args:
        shutdown_event: Asyncio-событие сигнала завершения.
        timeout_seconds: Таймаут в секундах.

    Returns:
        True если получен сигнал shutdown, False если истёк таймаут.
    """
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=timeout_seconds)
        return True
    except asyncio.TimeoutError:
        return False


class PidFileLock:
    """Context manager для PID-файла через fcntl.flock.

    Автоматически освобождается при любом завершении процесса включая crash.
    """

    def __init__(self, pid_file: str | pathlib.Path) -> None:
        self._path = pathlib.Path(pid_file)
        self._fh: object = None

    def acquire(self) -> None:
        """Получить эксклюзивную блокировку на PID-файл."""
        self._fh = open(self._path, "w")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            self._fh.close()
            self._fh = None
            raise RuntimeError(f"Воркер уже запущен (lock: {self._path})") from e
        self._fh.write(str(__import__("os").getpid()))
        self._fh.flush()

    def release(self) -> None:
        """Освободить блокировку и удалить PID-файл."""
        if self._fh:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass

    def __enter__(self) -> PidFileLock:
        self.acquire()
        return self

    def __exit__(self, *_) -> None:
        self.release()
