"""PostgreSQL-authoritative account context for campaign creation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.campaign_builder.money import (
    UnsupportedCampaignCurrencyError,
    campaign_currency_exponent,
)
from core.meta_api.account_tz import (
    CURRENCY_EVIDENCE_MAX_AGE,
    canonical_account_id,
    currency_evidence_is_fresh,
    validated_timezone_name,
)
from core.money import validated_currency_code

CampaignAccountContextState = Literal["ready", "stale", "unavailable"]
_ACCOUNT_ID_RE = re.compile(r"^[0-9]{1,32}$")


@dataclass(frozen=True, slots=True)
class CampaignAccountContext:
    """One immutable view of the selected cabinet's durable evidence."""

    account_id: str
    state: CampaignAccountContextState
    timezone_name: str | None
    currency: str | None
    currency_exponent: int | None
    observed_at: datetime | None
    next_start_date: date | None
    issue: str | None

    @property
    def is_ready(self) -> bool:
        return self.state == "ready"


class CampaignAccountContextError(RuntimeError):
    """Campaign creation cannot proceed with the durable account evidence."""

    def __init__(self, context: CampaignAccountContext) -> None:
        self.context = context
        super().__init__(context.issue or "campaign account context is unavailable")


def normalize_campaign_account_id(raw: str) -> str:
    """Return one numeric Meta account ID or raise before any DB work."""

    account_id = canonical_account_id(raw)
    if not _ACCOUNT_ID_RE.fullmatch(account_id):
        raise ValueError("act_id must contain 1..32 digits")
    return account_id


async def resolve_campaign_account_context(
    engine: AsyncEngine,
    *,
    account_id: str,
    now: datetime | None = None,
    max_age: timedelta = CURRENCY_EVIDENCE_MAX_AGE,
) -> CampaignAccountContext:
    """Resolve validated IANA/currency evidence without a live Meta fallback."""

    canonical_id = normalize_campaign_account_id(account_id)
    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT timezone_name, currency, currency_observed_at
                    FROM meta_account_snapshot
                    WHERE account_id = :account_id
                    LIMIT 1
                    """
                    ),
                    {"account_id": canonical_id},
                )
            )
            .mappings()
            .first()
        )

    timezone_name = validated_timezone_name(row["timezone_name"] if row else None)
    currency = validated_currency_code(row["currency"] if row else None)
    observed_at = row["currency_observed_at"] if row else None
    if timezone_name is None or currency is None or not isinstance(observed_at, datetime):
        return CampaignAccountContext(
            account_id=canonical_id,
            state="unavailable",
            timezone_name=timezone_name,
            currency=currency,
            currency_exponent=None,
            observed_at=observed_at if isinstance(observed_at, datetime) else None,
            next_start_date=None,
            issue="campaign_account_context_unavailable",
        )

    try:
        exponent = campaign_currency_exponent(currency)
    except UnsupportedCampaignCurrencyError:
        return CampaignAccountContext(
            account_id=canonical_id,
            state="unavailable",
            timezone_name=timezone_name,
            currency=currency,
            currency_exponent=None,
            observed_at=observed_at,
            next_start_date=None,
            issue="campaign_currency_exponent_unsupported",
        )

    local_tomorrow = observed_now.astimezone(ZoneInfo(timezone_name)).date() + timedelta(days=1)
    if not currency_evidence_is_fresh(observed_at, now=observed_now, max_age=max_age):
        return CampaignAccountContext(
            account_id=canonical_id,
            state="stale",
            timezone_name=timezone_name,
            currency=currency,
            currency_exponent=exponent,
            observed_at=observed_at,
            next_start_date=local_tomorrow,
            issue="campaign_account_context_stale",
        )

    return CampaignAccountContext(
        account_id=canonical_id,
        state="ready",
        timezone_name=timezone_name,
        currency=currency,
        currency_exponent=exponent,
        observed_at=observed_at,
        next_start_date=local_tomorrow,
        issue=None,
    )


async def require_campaign_account_context(
    engine: AsyncEngine,
    *,
    account_id: str,
    now: datetime | None = None,
) -> CampaignAccountContext:
    """Return ready evidence or fail before run/task/ledger writes."""

    context = await resolve_campaign_account_context(
        engine,
        account_id=account_id,
        now=now,
    )
    if not context.is_ready:
        raise CampaignAccountContextError(context)
    return context


__all__ = [
    "CampaignAccountContext",
    "CampaignAccountContextError",
    "CampaignAccountContextState",
    "normalize_campaign_account_id",
    "require_campaign_account_context",
    "resolve_campaign_account_context",
]
