# -*- coding: utf-8 -*-
"""Durable Meta account context and cabinet-day resolution.

``meta_account_snapshot`` in PostgreSQL is the sole authority for validated
IANA timezone and currency evidence. Numeric offsets, implicit USD and Redis
caches are intentionally absent: cache loss must never change money semantics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.money import (
    UnsupportedCurrencyExponentError,
    currency_exponent,
    validated_currency_code,
)
from core.observer.accounts import resolve_configured_ad_account_ids
from core.tasks.browser_fence import (
    BrowserFenceLeaseLost,
    BrowserOperationBlocked,
    BrowserOperationFence,
)

logger = logging.getLogger(__name__)

CURRENCY_EVIDENCE_MAX_AGE = timedelta(hours=24)
_CURRENCY_FUTURE_TOLERANCE = timedelta(minutes=5)


class CabinetTimezoneUnknownError(RuntimeError):
    """A money path requested a cabinet without a validated IANA timezone."""

    def __init__(self, account_ids: tuple[str, ...]) -> None:
        self.account_ids = account_ids
        joined = ", ".join(account_ids) or "<none>"
        super().__init__(f"IANA timezone is not confirmed for cabinet(s): {joined}")


class CabinetCurrencyUnknownError(RuntimeError):
    """A money path requested a cabinet without a validated currency."""

    def __init__(self, account_ids: tuple[str, ...]) -> None:
        self.account_ids = account_ids
        joined = ", ".join(account_ids) or "<none>"
        super().__init__(f"currency is not confirmed for cabinet(s): {joined}")


@dataclass(frozen=True, slots=True)
class CabinetDayResolution:
    """Per-account cabinet-day boundaries plus explicit unknown accounts."""

    account_ids: tuple[str, ...]
    timezone_names: dict[str, str]
    query_boundaries: dict[str, datetime]
    missing_account_ids: tuple[str, ...]

    @property
    def timezone_known(self) -> bool:
        return bool(self.account_ids) and not self.missing_account_ids

    @property
    def timezone_state(self) -> str:
        if not self.account_ids or self.missing_account_ids:
            return "unknown"
        return "single" if len(set(self.timezone_names.values())) == 1 else "mixed"

    @property
    def cabinet_timezone(self) -> str | None:
        if self.timezone_state != "single":
            return None
        return next(iter(self.timezone_names.values()))

    @property
    def display_timezone(self) -> str:
        names = sorted(set(self.timezone_names.values()))
        if len(names) == 1 and not self.missing_account_ids:
            return names[0]
        if names:
            return "per-account"
        return "UTC"


@dataclass(frozen=True, slots=True)
class AccountCurrencyResolution:
    """Per-account currency evidence with explicit unknown/mixed semantics."""

    account_ids: tuple[str, ...]
    currencies: dict[str, str]
    observed_at_by_account: dict[str, datetime]
    missing_account_ids: tuple[str, ...]

    @property
    def state(self) -> str:
        if not self.account_ids or self.missing_account_ids:
            return "unknown"
        return "single" if len(set(self.currencies.values())) == 1 else "mixed"

    @property
    def currency(self) -> str | None:
        if self.state != "single":
            return None
        return next(iter(self.currencies.values()))

    @property
    def observed_at(self) -> datetime | None:
        values = tuple(self.observed_at_by_account.values())
        return min(values) if values else None


@dataclass(frozen=True, slots=True)
class FetchedAccountContext:
    timezone_name: str | None
    currency: str | None


@dataclass(frozen=True, slots=True)
class RequiredCabinetDay:
    account_id: str
    timezone_name: str
    starts_at: datetime
    ends_at: datetime


def canonical_account_id(account_id: str) -> str:
    """Canonical DB key: Meta numeric id without the optional ``act_`` prefix."""
    return str(account_id or "").strip().removeprefix("act_")


def validated_timezone_name(raw: object) -> str | None:
    """Return a real IANA name or None; invalid persisted values fail closed."""
    name = str(raw or "").strip()
    if not name or len(name) > 128:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


def currency_evidence_is_fresh(
    observed_at: object,
    *,
    now: datetime,
    max_age: timedelta = CURRENCY_EVIDENCE_MAX_AGE,
) -> bool:
    """Whether one currency observation may authorize current money work."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    return bool(
        isinstance(observed_at, datetime)
        and observed_at.tzinfo is not None
        and now - max_age <= observed_at
        and observed_at <= now + _CURRENCY_FUTURE_TOLERANCE
    )


def cabinet_day_start_for_timezone(timezone_name: str, now: datetime) -> datetime:
    """Return local midnight in UTC using the IANA rules at midnight itself."""
    valid_name = validated_timezone_name(timezone_name)
    if valid_name is None:
        raise ValueError("unknown IANA timezone")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(ZoneInfo(valid_name))
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(UTC)


def cabinet_day_end_for_timezone(timezone_name: str, now: datetime) -> datetime:
    """Return the next local midnight in UTC (23/24/25 hours after start)."""
    valid_name = validated_timezone_name(timezone_name)
    if valid_name is None:
        raise ValueError("unknown IANA timezone")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(ZoneInfo(valid_name))
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (local_midnight + timedelta(days=1)).astimezone(UTC)


async def resolve_cabinet_days(
    engine: AsyncEngine,
    *,
    account_ids: list[str] | tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> CabinetDayResolution:
    """Resolve cabinet-day boundaries from persisted IANA names.

    Unknown accounts retain a UTC query estimate for read-only UI aggregation,
    but are listed in ``missing_account_ids``. Money callers must use
    :func:`resolve_required_cabinet_day`, which rejects that estimate.
    """
    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    # Дефолтный скоуп — наши кабинеты по конфигурации офферов. Следы прошлых
    # сканов скоуп не задают: у нового кабинета их нет, и он выпадал целиком.
    requested = (
        await resolve_configured_ad_account_ids(engine)
        if account_ids is None
        else list(account_ids)
    )
    canonical_ids = tuple(
        sorted(
            {
                canonical_account_id(account_id)
                for account_id in requested
                if canonical_account_id(account_id)
            }
        )
    )
    rows: list[Any] = []
    if canonical_ids:
        async with engine.connect() as conn:
            rows = list(
                (
                    await conn.execute(
                        text(
                            """
                            SELECT account_id, timezone_name
                            FROM meta_account_snapshot
                            WHERE account_id = ANY(CAST(:account_ids AS text[]))
                            """
                        ),
                        {"account_ids": list(canonical_ids)},
                    )
                ).mappings()
            )

    persisted = {str(row["account_id"]): row["timezone_name"] for row in rows}
    timezone_names: dict[str, str] = {}
    query_boundaries: dict[str, datetime] = {}
    missing: list[str] = []
    utc_estimate = observed_now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    for account_id in canonical_ids:
        timezone_name = validated_timezone_name(persisted.get(account_id))
        if timezone_name is None:
            missing.append(account_id)
            query_boundaries[account_id] = utc_estimate
            continue
        timezone_names[account_id] = timezone_name
        query_boundaries[account_id] = cabinet_day_start_for_timezone(timezone_name, observed_now)
    return CabinetDayResolution(
        account_ids=canonical_ids,
        timezone_names=timezone_names,
        query_boundaries=query_boundaries,
        missing_account_ids=tuple(missing),
    )


async def resolve_required_cabinet_day(
    engine: AsyncEngine,
    *,
    account_id: str,
    now: datetime | None = None,
) -> RequiredCabinetDay:
    """Resolve exactly one authoritative cabinet day or fail before money/FSM work."""
    observed_now = now or datetime.now(UTC)
    canonical_id = canonical_account_id(account_id)
    resolution = await resolve_cabinet_days(
        engine,
        account_ids=[canonical_id] if canonical_id else [],
        now=observed_now,
    )
    if not resolution.timezone_known or canonical_id not in resolution.timezone_names:
        missing = resolution.missing_account_ids or ((canonical_id,) if canonical_id else ())
        raise CabinetTimezoneUnknownError(tuple(missing))
    timezone_name = resolution.timezone_names[canonical_id]
    return RequiredCabinetDay(
        account_id=canonical_id,
        timezone_name=timezone_name,
        starts_at=resolution.query_boundaries[canonical_id],
        ends_at=cabinet_day_end_for_timezone(timezone_name, observed_now),
    )


async def resolve_account_currencies(
    engine: AsyncEngine,
    *,
    account_ids: list[str] | tuple[str, ...] | None = None,
    now: datetime | None = None,
    max_age: timedelta = CURRENCY_EVIDENCE_MAX_AGE,
) -> AccountCurrencyResolution:
    """Resolve confirmed currencies for the requested cabinet scope."""

    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    # Дефолтный скоуп — наши кабинеты по конфигурации офферов. Следы прошлых
    # сканов скоуп не задают: у нового кабинета их нет, и он выпадал целиком.
    requested = (
        await resolve_configured_ad_account_ids(engine)
        if account_ids is None
        else list(account_ids)
    )
    canonical_ids = tuple(
        sorted(
            {
                canonical_account_id(account_id)
                for account_id in requested
                if canonical_account_id(account_id)
            }
        )
    )
    rows: list[Any] = []
    if canonical_ids:
        async with engine.connect() as conn:
            rows = list(
                (
                    await conn.execute(
                        text(
                            """
                            SELECT account_id, currency, currency_observed_at
                            FROM meta_account_snapshot
                            WHERE account_id = ANY(CAST(:account_ids AS text[]))
                            """
                        ),
                        {"account_ids": list(canonical_ids)},
                    )
                ).mappings()
            )
    persisted = {str(row["account_id"]): row for row in rows}
    currencies: dict[str, str] = {}
    observed: dict[str, datetime] = {}
    missing: list[str] = []
    for account_id in canonical_ids:
        row = persisted.get(account_id)
        currency = validated_currency_code(row["currency"] if row is not None else None)
        if currency is not None:
            try:
                currency_exponent(currency)
            except UnsupportedCurrencyExponentError:
                currency = None
        observed_at = row["currency_observed_at"] if row is not None else None
        evidence_is_fresh = currency_evidence_is_fresh(
            observed_at,
            now=observed_now,
            max_age=max_age,
        )
        if currency is None or not evidence_is_fresh:
            missing.append(account_id)
            continue
        currencies[account_id] = currency
        observed[account_id] = observed_at
    return AccountCurrencyResolution(
        account_ids=canonical_ids,
        currencies=currencies,
        observed_at_by_account=observed,
        missing_account_ids=tuple(missing),
    )


async def resolve_required_account_currency(
    engine: AsyncEngine,
    *,
    account_id: str,
    now: datetime | None = None,
) -> str:
    """Return one confirmed cabinet currency or fail before money work."""

    canonical_id = canonical_account_id(account_id)
    resolution = await resolve_account_currencies(
        engine,
        account_ids=[canonical_id] if canonical_id else [],
        now=now,
    )
    currency = resolution.currencies.get(canonical_id)
    if resolution.state != "single" or currency is None:
        missing = resolution.missing_account_ids or ((canonical_id,) if canonical_id else ())
        raise CabinetCurrencyUnknownError(tuple(missing))
    return currency


async def fetch_account_timezone(client: Any, account_id: str) -> str | None:
    """Fetch and validate the authoritative IANA name from Meta Graph."""
    account = canonical_account_id(account_id)
    if not account:
        return None
    try:
        response = await client.execute_graph_call(
            method="GET",
            endpoint=f"/act_{account}",
            query_params={"fields": "timezone_name"},
            ad_account_id=account,
        )
    except Exception as exc:  # noqa: BLE001 - refresh is outside the money path
        logger.warning("account timezone fetch failed for act_%s: %s", account, exc)
        return None
    return validated_timezone_name(response.get("timezone_name"))


async def fetch_account_context(client: Any, account_id: str) -> FetchedAccountContext:
    """Fetch validated timezone and currency in one authoritative Graph read."""

    account = canonical_account_id(account_id)
    if not account:
        return FetchedAccountContext(timezone_name=None, currency=None)
    try:
        response = await client.execute_graph_call(
            method="GET",
            endpoint=f"/act_{account}",
            query_params={"fields": "timezone_name,currency"},
            ad_account_id=account,
        )
    except Exception as exc:  # noqa: BLE001 - refresh is outside the money path
        logger.warning("account context fetch failed for act_%s: %s", account, exc)
        return FetchedAccountContext(timezone_name=None, currency=None)
    return FetchedAccountContext(
        timezone_name=validated_timezone_name(response.get("timezone_name")),
        currency=validated_currency_code(response.get("currency")),
    )


async def persist_account_timezone(
    engine: AsyncEngine,
    *,
    account_id: str,
    timezone_name: str,
) -> bool:
    """Upsert a validated IANA name for a canonical cabinet ID."""
    canonical_id = canonical_account_id(account_id)
    valid_name = validated_timezone_name(timezone_name)
    if not canonical_id or len(canonical_id) > 32 or valid_name is None:
        return False
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO meta_account_snapshot
                    (account_id, timezone_name)
                VALUES (:account_id, :timezone_name)
                ON CONFLICT (account_id) DO UPDATE
                SET timezone_name = EXCLUDED.timezone_name,
                    updated_at = NOW()
                """
            ),
            {
                "account_id": canonical_id,
                "timezone_name": valid_name,
            },
        )
    return True


async def persist_account_context(
    engine: AsyncEngine,
    *,
    account_id: str,
    timezone_name: str | None,
    currency: str | None,
    observed_at: datetime | None = None,
) -> bool:
    """Upsert only validated account evidence; never replace it with a guess."""

    canonical_id = canonical_account_id(account_id)
    valid_timezone = validated_timezone_name(timezone_name)
    valid_currency = validated_currency_code(currency)
    if (
        not canonical_id
        or len(canonical_id) > 32
        or (valid_timezone is None and valid_currency is None)
    ):
        return False
    evidence_at = observed_at or datetime.now(UTC)
    if evidence_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO meta_account_snapshot
                    (account_id, timezone_name, currency, currency_observed_at)
                VALUES
                    (:account_id, :timezone_name, CAST(:currency AS VARCHAR),
                     CASE WHEN CAST(:currency AS VARCHAR) IS NULL
                          THEN NULL
                          ELSE CAST(:observed_at AS TIMESTAMPTZ)
                     END)
                ON CONFLICT (account_id) DO UPDATE
                SET timezone_name = COALESCE(
                        EXCLUDED.timezone_name,
                        meta_account_snapshot.timezone_name
                    ),
                    currency = COALESCE(
                        EXCLUDED.currency,
                        meta_account_snapshot.currency
                    ),
                    currency_observed_at = CASE
                        WHEN EXCLUDED.currency IS NOT NULL THEN EXCLUDED.currency_observed_at
                        ELSE meta_account_snapshot.currency_observed_at
                    END,
                    updated_at = NOW()
                """
            ),
            {
                "account_id": canonical_id,
                "timezone_name": valid_timezone,
                "currency": valid_currency,
                "observed_at": evidence_at,
            },
        )
    return True


async def refresh_account_timezones(engine: AsyncEngine, client: Any) -> int:
    """Best-effort refresh of durable timezone and currency evidence."""
    try:
        account_ids = await resolve_configured_ad_account_ids(engine)
    except Exception as exc:  # noqa: BLE001 - idle refresh never owns money decisions
        logger.warning("account timezone cabinet list failed: %s", exc)
        return 0
    updated = 0
    for account_id in account_ids:
        canonical_id = canonical_account_id(account_id)
        if not canonical_id:
            continue
        try:
            async with BrowserOperationFence(
                engine,
                operation_kind="account_context_refresh",
                target=canonical_id,
            ) as fence:
                context = await fetch_account_context(client, canonical_id)
                if context.timezone_name is None and context.currency is None:
                    # Молчаливый пропуск скрывал реальную причину: снимка нет,
                    # визард блокирует залив, а в логе ни строки (прод, 17.08.2026).
                    logger.warning(
                        "Meta не отдала пояс и валюту по кабинету act_%s — снимок не обновлён",
                        canonical_id,
                    )
                    continue
                await fence.assert_held()
                if await persist_account_context(
                    engine,
                    account_id=canonical_id,
                    timezone_name=context.timezone_name,
                    currency=context.currency,
                ):
                    updated += 1
        except BrowserOperationBlocked:
            logger.info("account context refresh deferred: browser maintenance is active")
            break
        except BrowserFenceLeaseLost:
            logger.warning(
                "account context refresh discarded after fence loss for act_%s",
                canonical_id,
            )
        except Exception:  # noqa: BLE001 - preserve the last durable known evidence
            logger.warning(
                "durable account context refresh failed for act_%s",
                canonical_id,
                exc_info=True,
            )
    if updated:
        logger.info("durable cabinet timezones refreshed: %d", updated)
    return updated


__all__ = [
    "CabinetDayResolution",
    "CURRENCY_EVIDENCE_MAX_AGE",
    "AccountCurrencyResolution",
    "CabinetCurrencyUnknownError",
    "CabinetTimezoneUnknownError",
    "FetchedAccountContext",
    "RequiredCabinetDay",
    "cabinet_day_end_for_timezone",
    "cabinet_day_start_for_timezone",
    "canonical_account_id",
    "currency_evidence_is_fresh",
    "fetch_account_context",
    "fetch_account_timezone",
    "persist_account_context",
    "persist_account_timezone",
    "refresh_account_timezones",
    "resolve_account_currencies",
    "resolve_cabinet_days",
    "resolve_required_account_currency",
    "resolve_required_cabinet_day",
    "validated_timezone_name",
]
