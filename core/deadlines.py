"""Absolute-deadline propagation across queue, Python, gRPC and browser fetch."""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator

_DEADLINE_MONOTONIC: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "fb_agent_deadline_monotonic", default=None
)


@contextmanager
def bind_absolute_deadline(deadline_at: datetime | None) -> Iterator[None]:
    """Bind one UTC deadline without relying on wall-clock changes mid-call."""
    if deadline_at is None:
        token = _DEADLINE_MONOTONIC.set(None)
    else:
        aware = deadline_at if deadline_at.tzinfo else deadline_at.replace(tzinfo=UTC)
        remaining = max(0.0, (aware - datetime.now(UTC)).total_seconds())
        token = _DEADLINE_MONOTONIC.set(time.monotonic() + remaining)
    try:
        yield
    finally:
        _DEADLINE_MONOTONIC.reset(token)


def remaining_deadline_seconds() -> float | None:
    deadline = _DEADLINE_MONOTONIC.get()
    return None if deadline is None else max(0.0, deadline - time.monotonic())


def bounded_timeout_seconds(default: float, *, floor: float = 0.001) -> float:
    remaining = remaining_deadline_seconds()
    if remaining is None:
        return default
    return max(floor, min(default, remaining))


def bounded_timeout_ms(default: int, *, floor: int = 1) -> int:
    remaining = remaining_deadline_seconds()
    if remaining is None:
        return default
    return max(floor, min(default, int(remaining * 1000)))


__all__ = [
    "bind_absolute_deadline",
    "bounded_timeout_ms",
    "bounded_timeout_seconds",
    "remaining_deadline_seconds",
]
