# -*- coding: utf-8 -*-
"""Pure-функции для парсинга retention_policy.

Отдельно от I/O — легко покрывается unit-тестами.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_UNIT_TO_SECONDS = {
    "second": 1,
    "seconds": 1,
    "minute": 60,
    "minutes": 60,
    "hour": 3600,
    "hours": 3600,
    "day": 86400,
    "days": 86400,
    "week": 7 * 86400,
    "weeks": 7 * 86400,
    "month": 30 * 86400,
    "months": 30 * 86400,
    "year": 365 * 86400,
    "years": 365 * 86400,
}

_DURATION_RE = re.compile(r"^\s*(\d+)\s+(\w+)\s*$")


class RetentionParseError(ValueError):
    """Нераспознанная строка retention."""


def parse_duration(value: str) -> timedelta:
    """'14 days' → timedelta(days=14). 'forever', 'immediate', 'redis_ttl_only' → исключения."""
    if not isinstance(value, str):
        raise RetentionParseError(f"Ожидалась строка, получил {type(value).__name__}")
    v = value.strip().lower()
    if v in ("forever", "immediate", "redis_ttl_only"):
        raise RetentionParseError(f"Специальное значение '{value}' не имеет timedelta")

    match = _DURATION_RE.match(v)
    if not match:
        raise RetentionParseError(f"Не распознал retention: {value!r}")

    n = int(match.group(1))
    unit = match.group(2)
    if unit not in _UNIT_TO_SECONDS:
        raise RetentionParseError(f"Неизвестная единица: {unit}")
    return timedelta(seconds=n * _UNIT_TO_SECONDS[unit])


def cutoff_datetime(retention: str, *, now: datetime | None = None) -> datetime:
    """Вернёт datetime границы (now - parse_duration). UTC, TZ-aware."""
    base = now or datetime.now(timezone.utc)
    delta = parse_duration(retention)
    return base - delta


def is_special(value: str) -> bool:
    """True для специальных меток forever / immediate / redis_ttl_only."""
    if not isinstance(value, str):
        return False
    return value.strip().lower() in ("forever", "immediate", "redis_ttl_only")


_DEFAULT_RETENTION: dict[str, str] = {
    "ad_library_scan": "14 days",
    "ad_library_ad_orphan": "14 days",
    "ad_library_snapshot": "14 days",
    "ad_library_media_orphan": "immediate",
    "ad_metrics": "90 days",
    "alert_events": "365 days",
    "scan_runs": "30 days",
    "meta_api_audit_log": "30 days",
    "meta_api_webhook_event": "90 days",
    "tracker_postback": "60 days",
    "task_queue_completed": "30 days",
    "task_queue_failed": "90 days",
    "enable_recommendations": "30 days",
    "telegram_invites_expired": "30 days",
    "cabinet_day_archives": "365 days",
    "ad_library_winner_archive": "forever",
    "ai_cache": "redis_ttl_only",
}


def get_default_policy() -> dict[str, str]:
    """Возвращает дефолтную retention policy (на случай если её нет в system_config)."""
    return dict(_DEFAULT_RETENTION)
