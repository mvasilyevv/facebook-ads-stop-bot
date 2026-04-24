# -*- coding: utf-8 -*-
"""Общая блокировка доступа к Vision-браузеру через PostgreSQL advisory lock."""

from __future__ import annotations

import asyncio
import logging
import time
import zlib
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text

from core.db import get_engine

logger = logging.getLogger(__name__)

_LOCK_NAMESPACE = 0x4642
_DEFAULT_SCOPE = "vision-profile:default"


class BrowserLockTimeoutError(TimeoutError):
    """Браузер занят другой операцией дольше допустимого времени ожидания."""


@dataclass(frozen=True)
class BrowserLockLease:
    """Информация о занятой блокировке браузера."""

    scope: str
    owner: str
    waited_seconds: float


def _scope_key(scope: str) -> int:
    """Возвращает стабильный int32-ключ для advisory lock."""
    normalized = (scope or _DEFAULT_SCOPE).strip() or _DEFAULT_SCOPE
    return zlib.crc32(normalized.encode("utf-8")) & 0x7FFFFFFF


@asynccontextmanager
async def acquire_browser_lock(
    *,
    scope: str = _DEFAULT_SCOPE,
    owner: str,
    timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 0.5,
):
    """Занимает эксклюзивный lock на общий браузер для high-level операции.

    Lock держится на отдельном соединении до выхода из контекста, поэтому
    защищает последовательность из нескольких gRPC-вызовов внутри одного flow.
    """
    key = _scope_key(scope)
    started_at = time.monotonic()
    engine = get_engine()

    async with engine.connect() as connection:
        while True:
            result = await connection.execute(
                text("SELECT pg_try_advisory_lock(:namespace, :key)"),
                {"namespace": _LOCK_NAMESPACE, "key": key},
            )
            if bool(result.scalar()):
                waited = time.monotonic() - started_at
                if waited >= poll_interval_seconds:
                    logger.info(
                        "Блокировка браузера получена: owner=%s, ожидание=%.1f сек",
                        owner,
                        waited,
                    )
                lease = BrowserLockLease(scope=scope, owner=owner, waited_seconds=waited)
                try:
                    yield lease
                finally:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:namespace, :key)"),
                        {"namespace": _LOCK_NAMESPACE, "key": key},
                    )
                    logger.debug("Блокировка браузера освобождена: owner=%s", owner)
                return

            waited = time.monotonic() - started_at
            if waited >= timeout_seconds:
                raise BrowserLockTimeoutError(
                    f"Браузер занят другой операцией более {int(timeout_seconds)} сек"
                )
            await asyncio.sleep(poll_interval_seconds)
