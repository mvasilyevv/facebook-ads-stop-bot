# -*- coding: utf-8 -*-
"""Durable curator grace for confirmed ad activation.

PostgreSQL is the only authority.  A grace record is cabinet-day scoped and is
stored on ``ad_alert_state`` before the activating task becomes terminal.  The
observer reads it together with the FSM snapshot; there is no Redis marker,
TTL, scan, serializer or compatibility path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.meta_api.account_tz import (
    CabinetCurrencyUnknownError,
    CabinetTimezoneUnknownError,
    canonical_account_id,
    resolve_required_account_currency,
    resolve_required_cabinet_day,
)
from core.money import (
    InvalidCurrencyAmountError,
    UnsupportedCurrencyExponentError,
    currency_exponent,
    require_currency_exponent,
    require_exact_currency_amount,
    validated_currency_code,
)
from core.observer.cabinet_supervisor import CabinetLease
from core.scanner.status import is_delivery_activatable


class EnableGraceUnsafeError(ValueError):
    """The current durable state cannot safely create the requested grace."""


@dataclass(frozen=True, slots=True)
class EnableGrace:
    """One coherent, cabinet-day-scoped grace record."""

    until: datetime
    spend_cap: Decimal
    baseline_spend: Decimal
    cabinet_day_start: datetime
    currency: str
    currency_exponent: int


@dataclass(frozen=True, slots=True)
class PreparedEnableGrace:
    """Validated grace plus the canonical PostgreSQL ad identity."""

    ad_id: uuid.UUID
    fb_ad_id: str
    grace: EnableGrace


def grace_is_active(
    grace: EnableGrace,
    *,
    now: datetime,
    spend: Decimal | None,
    cabinet_day_start: datetime,
    currency: str,
    currency_exponent: int,
) -> bool:
    """Return true only while time, day and cumulative-spend evidence agree.

    Missing spend is unknown, not zero, and therefore cannot suppress a money
    rule.  A cumulative reset below the activation baseline also ends grace
    fail-closed.
    """
    if now.tzinfo is None or cabinet_day_start.tzinfo is None:
        raise ValueError("grace evaluation timestamps must be timezone-aware")
    if grace.cabinet_day_start != cabinet_day_start:
        return False
    confirmed_currency, confirmed_exponent = require_currency_exponent(
        currency,
        currency_exponent,
    )
    if grace.currency != confirmed_currency or grace.currency_exponent != confirmed_exponent:
        return False
    if grace.until <= now or spend is None:
        return False
    current_spend = require_exact_currency_amount(
        spend,
        currency=confirmed_currency,
        exponent=confirmed_exponent,
        field="spend",
    )
    if current_spend < grace.baseline_spend:
        return False
    return current_spend < grace.spend_cap


async def prepare_enable_grace(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
    ad_account_id: str,
    requested_spend_cap: Decimal | str,
    grace_seconds: int,
    now: datetime | None = None,
    require_disabled: bool = True,
) -> PreparedEnableGrace:
    """Revalidate status, current spend, CPA and cabinet day before activation.

    ``requested_spend_cap`` is an absolute daily cap (normally one CPA), never
    an allowance added to already-spent money.  A lower current CPA may tighten
    the cap; it can never increase the reviewed intent.
    """
    observed_at = now or datetime.now(UTC)
    if observed_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if int(grace_seconds) <= 0:
        raise EnableGraceUnsafeError("grace_seconds must be positive")

    account_id = canonical_account_id(ad_account_id)
    if not account_id:
        raise EnableGraceUnsafeError("ad account identity is missing")
    try:
        cabinet_day = await resolve_required_cabinet_day(
            engine,
            account_id=account_id,
            now=observed_at,
        )
    except CabinetTimezoneUnknownError as exc:
        raise EnableGraceUnsafeError(
            f"IANA timezone is not confirmed for account {account_id}"
        ) from exc
    try:
        account_currency = await resolve_required_account_currency(
            engine,
            account_id=account_id,
            now=observed_at,
        )
        account_currency_exponent = currency_exponent(account_currency)
    except (CabinetCurrencyUnknownError, UnsupportedCurrencyExponentError) as exc:
        raise EnableGraceUnsafeError(f"currency is not confirmed for account {account_id}") from exc
    cabinet_day_start = cabinet_day.starts_at

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT fa.id,
                           fa.delivery_status,
                           state.alert_state,
                           latest.spend,
                           latest.currency AS metric_currency,
                           rule.cpa_threshold,
                           rule.currency AS rule_currency
                    FROM fb_ads fa
                    JOIN fb_adsets adset ON adset.id = fa.adset_id
                    JOIN fb_campaigns campaign ON campaign.id = adset.campaign_id
                    LEFT JOIN ad_alert_state state ON state.ad_id = fa.id
                    LEFT JOIN offer_rules rule ON rule.offer_id = campaign.offer_id
                    LEFT JOIN LATERAL (
                        SELECT metric.spend, metric.currency
                        FROM ad_metrics metric
                        WHERE metric.ad_id = fa.id
                          AND metric.cycle_ts >= :cabinet_day_start
                          AND metric.cycle_ts <= :observed_at
                        ORDER BY metric.cycle_ts DESC
                        LIMIT 1
                    ) latest ON TRUE
                    WHERE fa.fb_ad_id = :fb_ad_id
                      AND campaign.ad_account_id = :account_id
                    """
                ),
                {
                    "fb_ad_id": str(fb_ad_id),
                    "account_id": account_id,
                    "cabinet_day_start": cabinet_day_start,
                    "observed_at": observed_at,
                },
            )
        ).first()
    if row is None:
        raise EnableGraceUnsafeError("ad is absent from the requested cabinet")
    if require_disabled and (
        not is_delivery_activatable(row.delivery_status)
        or str(row.alert_state or "").strip().lower() != "disabled"
    ):
        raise EnableGraceUnsafeError("ad is no longer confirmed OFF and disabled")
    if row.spend is None:
        raise EnableGraceUnsafeError("current cabinet-day spend is unknown")
    if validated_currency_code(row.metric_currency) != account_currency:
        raise EnableGraceUnsafeError("latest spend currency does not match the cabinet")
    if validated_currency_code(row.rule_currency) != account_currency:
        raise EnableGraceUnsafeError("offer CPA currency does not match the cabinet")

    try:
        baseline_spend = require_exact_currency_amount(
            row.spend,
            currency=account_currency,
            exponent=account_currency_exponent,
            field="baseline_spend",
        )
        spend_cap = require_exact_currency_amount(
            requested_spend_cap,
            currency=account_currency,
            exponent=account_currency_exponent,
            field="spend_cap",
            allow_zero=False,
        )
        if row.cpa_threshold is not None:
            current_cpa = require_exact_currency_amount(
                row.cpa_threshold,
                currency=account_currency,
                exponent=account_currency_exponent,
                field="current_cpa",
                allow_zero=False,
            )
            spend_cap = min(spend_cap, current_cpa)
    except InvalidCurrencyAmountError as exc:
        raise EnableGraceUnsafeError(str(exc)) from exc
    if baseline_spend >= spend_cap:
        raise EnableGraceUnsafeError(
            "current cabinet-day spend has already reached the absolute grace cap"
        )

    until = min(
        observed_at + timedelta(seconds=int(grace_seconds)),
        cabinet_day.ends_at,
    )
    if until <= observed_at:
        raise EnableGraceUnsafeError("cabinet day ended before grace could start")
    return PreparedEnableGrace(
        ad_id=uuid.UUID(str(row.id)),
        fb_ad_id=str(fb_ad_id),
        grace=EnableGrace(
            until=until,
            spend_cap=spend_cap,
            baseline_spend=baseline_spend,
            cabinet_day_start=cabinet_day_start,
            currency=account_currency,
            currency_exponent=account_currency_exponent,
        ),
    )


async def persist_enable_grace(
    connection: AsyncConnection,
    *,
    prepared: PreparedEnableGrace,
) -> None:
    """Persist a coherent grace record inside the caller's transaction."""
    grace = prepared.grace
    result = await connection.execute(
        text(
            """
            INSERT INTO ad_alert_state (
                ad_id,
                alert_state,
                enable_grace_until,
                enable_grace_spend_cap,
                enable_grace_baseline_spend,
                enable_grace_cabinet_day_start,
                enable_grace_currency,
                enable_grace_currency_exponent
            )
            VALUES (
                :ad_id,
                'normal',
                :grace_until,
                :spend_cap,
                :baseline_spend,
                :cabinet_day_start,
                :currency,
                :currency_exponent
            )
            ON CONFLICT (ad_id) DO UPDATE
            SET enable_grace_until = EXCLUDED.enable_grace_until,
                enable_grace_spend_cap = EXCLUDED.enable_grace_spend_cap,
                enable_grace_baseline_spend = EXCLUDED.enable_grace_baseline_spend,
                enable_grace_cabinet_day_start = EXCLUDED.enable_grace_cabinet_day_start,
                enable_grace_currency = EXCLUDED.enable_grace_currency,
                enable_grace_currency_exponent = EXCLUDED.enable_grace_currency_exponent,
                updated_at = NOW()
            WHERE ad_alert_state.ad_id = EXCLUDED.ad_id
            RETURNING ad_id
            """
        ),
        {
            "ad_id": prepared.ad_id,
            "grace_until": grace.until,
            "spend_cap": grace.spend_cap,
            "baseline_spend": grace.baseline_spend,
            "cabinet_day_start": grace.cabinet_day_start,
            "currency": grace.currency,
            "currency_exponent": grace.currency_exponent,
        },
    )
    if result.first() is None:
        raise RuntimeError(f"enable grace was not persisted for {prepared.fb_ad_id}")


async def clear_enable_grace_for_currency_mismatch(
    engine: AsyncEngine,
    *,
    ad_id: uuid.UUID,
    currency: str,
    currency_exponent: int,
    cabinet_lease: CabinetLease | None = None,
) -> bool:
    """Clear stale monetary grace identity before the observer decides."""

    confirmed_currency, confirmed_exponent = require_currency_exponent(
        currency,
        currency_exponent,
    )
    async with engine.begin() as connection:
        if cabinet_lease is not None:
            owned = (
                await connection.execute(
                    text(
                        """
                        SELECT 1
                        FROM cabinet_runtime
                        WHERE ad_account_id = :account
                          AND owner_instance = :owner
                          AND lease_token = :token
                          AND lease_expires_at > clock_timestamp()
                        FOR UPDATE
                        """
                    ),
                    {
                        "account": cabinet_lease.ad_account_id,
                        "owner": cabinet_lease.owner_instance,
                        "token": cabinet_lease.lease_token,
                    },
                )
            ).first()
            if owned is None:
                raise RuntimeError(
                    f"cabinet fence rejected for account={cabinet_lease.ad_account_id}"
                )
        result = await connection.execute(
            text(
                """
                UPDATE ad_alert_state
                SET enable_grace_until = NULL,
                    enable_grace_spend_cap = NULL,
                    enable_grace_baseline_spend = NULL,
                    enable_grace_cabinet_day_start = NULL,
                    enable_grace_currency = NULL,
                    enable_grace_currency_exponent = NULL,
                    updated_at = NOW()
                WHERE ad_id = :ad_id
                  AND enable_grace_until IS NOT NULL
                  AND (
                      enable_grace_currency IS DISTINCT FROM :currency
                      OR enable_grace_currency_exponent IS DISTINCT FROM :currency_exponent
                  )
                """
            ),
            {
                "ad_id": ad_id,
                "currency": confirmed_currency,
                "currency_exponent": confirmed_exponent,
            },
        )
    return bool(result.rowcount)


__all__ = [
    "EnableGrace",
    "EnableGraceUnsafeError",
    "PreparedEnableGrace",
    "clear_enable_grace_for_currency_mismatch",
    "grace_is_active",
    "persist_enable_grace",
    "prepare_enable_grace",
]
