"""Canonical Facebook re-login incident shared by scan and mutation paths."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.meta_api.identity import require_ad_account_id
from core.telegram.worker_notify import (
    notify_recurring_incident,
    notify_recurring_incident_in_transaction,
)

LOGIN_REQUIRED_INCIDENT_PREFIX = "observer:login_required:"


def login_required_incident_key(ad_account_id: str) -> str:
    """Return the stable per-cabinet key used for dedupe and recovery."""
    return f"{LOGIN_REQUIRED_INCIDENT_PREFIX}{require_ad_account_id(ad_account_id)}"


def _notification_kwargs(ad_account_id: str) -> dict[str, Any]:
    account_id = require_ad_account_id(ad_account_id)
    return {
        "incident_key": login_required_incident_key(account_id),
        "audience": "all",
        "event_type": "observer_login_required",
        "severity": "critical",
        "title": "В Facebook нужно войти снова",
        "summary": f"Кабинет: {account_id}",
        "risk": "Пока не войдёшь, скан не идёт и авто-стоп не сработает",
        "lines": ["Открой удалённый рабочий стол и войди в Facebook"],
        "resource_type": "ad_account",
        "resource_id": account_id,
    }


async def notify_login_required_incident(
    engine: AsyncEngine,
    *,
    ad_account_id: str,
) -> bool:
    """Open or refresh the single active re-login incident for a cabinet."""
    return await notify_recurring_incident(engine, **_notification_kwargs(ad_account_id))


async def notify_login_required_incident_in_transaction(
    conn: AsyncConnection,
    *,
    ad_account_id: str,
) -> bool:
    """Project the same incident atomically with a failed Meta task."""
    return await notify_recurring_incident_in_transaction(
        conn,
        **_notification_kwargs(ad_account_id),
    )


__all__ = [
    "LOGIN_REQUIRED_INCIDENT_PREFIX",
    "login_required_incident_key",
    "notify_login_required_incident",
    "notify_login_required_incident_in_transaction",
]
