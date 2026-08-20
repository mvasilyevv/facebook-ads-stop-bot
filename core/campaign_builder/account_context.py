"""PostgreSQL-authoritative account context for campaign creation."""

from __future__ import annotations

import re
from collections.abc import Mapping
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
from core.meta_api.account_status import (
    ACCOUNT_STATUS_ACTIVE,
    validated_account_status,
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

# Стабильные машинные коды причин. В интерфейс уходит не код, а текст из
# ``campaign_account_context_message``: код живёт в логе и в ветвлениях кода.
CAMPAIGN_ACCOUNT_CONTEXT_UNAVAILABLE = "campaign_account_context_unavailable"
CAMPAIGN_ACCOUNT_CONTEXT_STALE = "campaign_account_context_stale"
CAMPAIGN_CURRENCY_EXPONENT_UNSUPPORTED = "campaign_currency_exponent_unsupported"
CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE = "campaign_ad_account_not_active"
CAMPAIGN_ACCOUNT_STATUS_UNKNOWN = "campaign_account_status_unknown"


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
    # Подтверждённый код статуса кабинета; None означает «Meta не подтвердила»,
    # а не «активен».
    account_status: int | None = None

    @property
    def is_ready(self) -> bool:
        return self.state == "ready"

    @property
    def blocked_by_account_status(self) -> bool:
        """Кабинет отвергнут по собственному статусу, а не по нехватке снимка."""

        return self.issue == CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE


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


def campaign_account_context_from_row(
    row: Mapping[str, object] | None,
    *,
    account_id: str,
    now: datetime,
    max_age: timedelta = CURRENCY_EVIDENCE_MAX_AGE,
) -> CampaignAccountContext:
    """Спроецировать строку снимка в состояние контекста без обращения к БД.

    Порядок ветвей — от самого конкретного факта к самому общему. Отключённый
    кабинет называется первым: пока Meta не даёт создавать рекламу, отсутствие
    валюты или пояса оператору ничего не объясняет.
    """

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    timezone_name = validated_timezone_name(row["timezone_name"] if row else None)
    currency = validated_currency_code(row["currency"] if row else None)
    raw_observed_at = row["currency_observed_at"] if row else None
    observed_at = raw_observed_at if isinstance(raw_observed_at, datetime) else None
    account_status = validated_account_status(row["account_status"] if row else None)
    raw_status_observed_at = row["account_status_observed_at"] if row else None
    status_observed_at = (
        raw_status_observed_at if isinstance(raw_status_observed_at, datetime) else None
    )

    def context(
        *,
        state: CampaignAccountContextState,
        issue: str | None,
        currency_exponent: int | None = None,
        next_start_date: date | None = None,
    ) -> CampaignAccountContext:
        return CampaignAccountContext(
            account_id=account_id,
            state=state,
            timezone_name=timezone_name,
            currency=currency,
            currency_exponent=currency_exponent,
            observed_at=observed_at,
            next_start_date=next_start_date,
            issue=issue,
            account_status=account_status,
        )

    if account_status is not None and account_status != ACCOUNT_STATUS_ACTIVE:
        return context(state="unavailable", issue=CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE)

    if timezone_name is None or currency is None or observed_at is None:
        return context(state="unavailable", issue=CAMPAIGN_ACCOUNT_CONTEXT_UNAVAILABLE)

    try:
        exponent = campaign_currency_exponent(currency)
    except UnsupportedCampaignCurrencyError:
        return context(state="unavailable", issue=CAMPAIGN_CURRENCY_EXPONENT_UNSUPPORTED)

    if account_status is None or status_observed_at is None:
        # Статус кабинета не подтверждён. Считать его активным «по умолчанию»
        # означает вернуться ровно к отказу 20.08.2026.
        return context(
            state="unavailable",
            issue=CAMPAIGN_ACCOUNT_STATUS_UNKNOWN,
            currency_exponent=exponent,
        )

    local_tomorrow = now.astimezone(ZoneInfo(timezone_name)).date() + timedelta(days=1)
    evidence_is_fresh = currency_evidence_is_fresh(
        observed_at, now=now, max_age=max_age
    ) and currency_evidence_is_fresh(status_observed_at, now=now, max_age=max_age)
    if not evidence_is_fresh:
        return context(
            state="stale",
            issue=CAMPAIGN_ACCOUNT_CONTEXT_STALE,
            currency_exponent=exponent,
            next_start_date=local_tomorrow,
        )

    return context(
        state="ready",
        issue=None,
        currency_exponent=exponent,
        next_start_date=local_tomorrow,
    )


async def resolve_campaign_account_context(
    engine: AsyncEngine,
    *,
    account_id: str,
    now: datetime | None = None,
    max_age: timedelta = CURRENCY_EVIDENCE_MAX_AGE,
) -> CampaignAccountContext:
    """Resolve validated IANA/currency/status evidence without a live Meta fallback."""

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
                    SELECT timezone_name, currency, currency_observed_at,
                           account_status, account_status_observed_at
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

    return campaign_account_context_from_row(
        row,
        account_id=canonical_id,
        now=observed_now,
        max_age=max_age,
    )


# Причина для оператора: что случилось и почему запуск не пройдёт. Номер кода
# Meta и машинный идентификатор причины сюда не попадают — они остаются в логе.
_ISSUE_MESSAGES: dict[str, str] = {
    CAMPAIGN_ACCOUNT_CONTEXT_UNAVAILABLE: ("Часовой пояс и валюта кабинета не подтверждены Meta"),
    CAMPAIGN_ACCOUNT_CONTEXT_STALE: (
        "Снимок кабинета устарел — дождитесь свежего подтверждения Meta"
    ),
    CAMPAIGN_CURRENCY_EXPONENT_UNSUPPORTED: (
        "Валюта кабинета не поддерживается для создания кампаний"
    ),
    CAMPAIGN_ACCOUNT_STATUS_UNKNOWN: (
        "Статус кабинета не подтверждён Meta — запуск заблокирован до подтверждения"
    ),
}
_ACCOUNT_STATUS_MESSAGES: dict[int, str] = {
    2: "Кабинет отключён Meta — создавать и редактировать рекламу нельзя",
    3: "У кабинета неоплаченный счёт — Meta не даёт создавать рекламу",
    7: "Кабинет на проверке безопасности Meta — создавать рекламу нельзя",
    8: "Кабинет ждёт списания оплаты — Meta не даёт создавать рекламу",
    9: "У кабинета просрочена оплата — Meta не даёт создавать рекламу",
    100: "Кабинет закрывается — создавать рекламу нельзя",
    101: "Кабинет закрыт — создавать рекламу нельзя",
}
_NOT_ACTIVE_FALLBACK = "Кабинет не в активном состоянии — Meta не даёт создавать рекламу"
_UNKNOWN_ISSUE_FALLBACK = "Контекст кабинета не подтверждён — запуск заблокирован"


def campaign_account_context_message(context: CampaignAccountContext) -> str | None:
    """Причина отказа на языке оператора; None, если отказывать не в чем."""

    if context.issue is None:
        return None
    if context.issue == CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE:
        if context.account_status is None:
            return _NOT_ACTIVE_FALLBACK
        return _ACCOUNT_STATUS_MESSAGES.get(context.account_status, _NOT_ACTIVE_FALLBACK)
    return _ISSUE_MESSAGES.get(context.issue, _UNKNOWN_ISSUE_FALLBACK)


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
    "CAMPAIGN_ACCOUNT_CONTEXT_STALE",
    "CAMPAIGN_ACCOUNT_CONTEXT_UNAVAILABLE",
    "CAMPAIGN_ACCOUNT_STATUS_UNKNOWN",
    "CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE",
    "CAMPAIGN_CURRENCY_EXPONENT_UNSUPPORTED",
    "CampaignAccountContext",
    "CampaignAccountContextError",
    "CampaignAccountContextState",
    "campaign_account_context_from_row",
    "campaign_account_context_message",
    "normalize_campaign_account_id",
    "require_campaign_account_context",
    "resolve_campaign_account_context",
]
