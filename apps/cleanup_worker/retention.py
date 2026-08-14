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


# Сроки подобраны под одно-хостовый runtime с ограниченным диском: храним
# ровно столько, сколько нужно для разбора инцидента и сравнения периодов.
# Дольше всех живёт то, чем доказывают денежное действие, а не технический шум.
_DEFAULT_RETENTION: dict[str, str] = {
    # Самая объёмная таблица: снимок по каждому объявлению за каждый скан.
    # Ровно 30 дней ставить нельзя: в аналитике есть пресет «30 дней», а
    # партиции отбрасываются целиком и сутки кабинета считаются по его
    # таймзоне — на стыке пресет молча показывал бы неполный период.
    # 45 дней дают запас на границы и всё равно вдвое меньше прежних 90.
    "ad_metrics": "45 days",
    "alert_events": "120 days",
    "scan_runs": "30 days",
    # Аудит мутаций в Meta — доказательство того, что и когда мы отправили.
    # Ниже месяца опускать нельзя: это последний источник правды при споре
    # «система остановила» против «остановил кто-то другой».
    "meta_api_audit_log": "30 days",
    "adsetpro_postback_events": "45 days",
    "task_queue_completed": "30 days",
    # Упавшие задачи разбирают дольше успешных, но не кварталами.
    "task_queue_failed": "45 days",
    "adset_duplicate_previews_expired": "immediate",
    "browser_operation_capabilities_expired": "immediate",
    "telegram_invites_expired": "30 days",
    "operator_revision_events": "7 days",
    # Durable notification/idempotency boundaries.  Only terminal rows are
    # eligible; active incidents, queued deliveries and leased webhook work are
    # preserved regardless of age.
    # Закрытые инциденты — история денежных решений, поэтому переживают
    # остальное, но полугода достаточно: спор о конкретной откруте настолько
    # старым не бывает.
    "incidents_terminal": "180 days",
    "notification_events_terminal": "90 days",
    "telegram_action_tokens_terminal": "45 days",
    "telegram_navigation_tokens_terminal": "30 days",
    "telegram_updates_terminal": "30 days",
    "telegram_command_replies_terminal": "30 days",
    "ai_cache": "redis_ttl_only",
}


def get_default_policy() -> dict[str, str]:
    """Возвращает дефолтную retention policy (на случай если её нет в system_config)."""
    return dict(_DEFAULT_RETENTION)
