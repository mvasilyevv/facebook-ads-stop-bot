"""Durable owner display-timezone preference.

The module is deliberately separate from cabinet timezone evidence and from
Telegram notification quiet-hours preferences. It owns presentation only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from core.models.operator.display_preference import OperatorDisplayPreference
from core.models.telegram.recipient import TelegramRecipient
from core.operator.timezones import (
    DEFAULT_OPERATOR_DISPLAY_TIMEZONE,
    validate_iana_timezone,
)


class ActiveOwnerRequiredError(PermissionError):
    """The authenticated identity is not an active interactive owner."""


@dataclass(frozen=True)
class OperatorDisplayPreferenceSnapshot:
    timezone_name: str
    updated_at: datetime


async def _active_owner_recipient_id(
    session: AsyncSession,
    *,
    telegram_user_id: int,
):
    if telegram_user_id <= 0:
        raise ActiveOwnerRequiredError("active owner identity is required")
    recipient_id = await session.scalar(
        select(TelegramRecipient.id)
        .where(
            TelegramRecipient.telegram_user_id == telegram_user_id,
            TelegramRecipient.role == "owner",
            TelegramRecipient.revoked_at.is_(None),
        )
        .limit(1)
    )
    if recipient_id is None:
        raise ActiveOwnerRequiredError("active owner identity is required")
    return recipient_id


def _snapshot(row: OperatorDisplayPreference) -> OperatorDisplayPreferenceSnapshot:
    return OperatorDisplayPreferenceSnapshot(
        timezone_name=str(row.timezone_name),
        updated_at=row.updated_at,
    )


async def get_operator_display_preference(
    engine: AsyncEngine,
    *,
    telegram_user_id: int,
) -> OperatorDisplayPreferenceSnapshot:
    """Load the owner preference, provisioning the reviewed server default once."""

    async with AsyncSession(engine) as session, session.begin():
        owner_recipient_id = await _active_owner_recipient_id(
            session,
            telegram_user_id=telegram_user_id,
        )
        await session.execute(
            pg_insert(OperatorDisplayPreference)
            .values(
                owner_recipient_id=owner_recipient_id,
                timezone_name=DEFAULT_OPERATOR_DISPLAY_TIMEZONE,
            )
            .on_conflict_do_nothing(index_elements=[OperatorDisplayPreference.owner_recipient_id])
        )
        row = await session.scalar(
            select(OperatorDisplayPreference).where(
                OperatorDisplayPreference.owner_recipient_id == owner_recipient_id
            )
        )
        if row is None:  # pragma: no cover - protected by the same transaction
            raise RuntimeError("operator display preference was not persisted")
        return _snapshot(row)


async def put_operator_display_preference(
    engine: AsyncEngine,
    *,
    telegram_user_id: int,
    timezone_name: str,
) -> OperatorDisplayPreferenceSnapshot:
    """Atomically create or replace the authenticated owner's preference."""

    validated_timezone = validate_iana_timezone(timezone_name)
    async with AsyncSession(engine) as session, session.begin():
        owner_recipient_id = await _active_owner_recipient_id(
            session,
            telegram_user_id=telegram_user_id,
        )
        statement = (
            pg_insert(OperatorDisplayPreference)
            .values(
                owner_recipient_id=owner_recipient_id,
                timezone_name=validated_timezone,
            )
            .on_conflict_do_update(
                index_elements=[OperatorDisplayPreference.owner_recipient_id],
                set_={
                    "timezone_name": validated_timezone,
                    "updated_at": func.now(),
                },
            )
            .returning(OperatorDisplayPreference)
        )
        row = (await session.scalars(statement)).one()
        return _snapshot(row)


__all__ = [
    "ActiveOwnerRequiredError",
    "DEFAULT_OPERATOR_DISPLAY_TIMEZONE",
    "OperatorDisplayPreferenceSnapshot",
    "get_operator_display_preference",
    "put_operator_display_preference",
    "validate_iana_timezone",
]
