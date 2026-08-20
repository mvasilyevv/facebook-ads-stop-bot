# -*- coding: utf-8 -*-
"""Инвариант: залив в неактивный кабинет отвергается ДО отправки в Meta.

20.08.2026 отказ приходил постфактум: две кампании создались, на третьей Meta
ответила «Отключенные аккаунты не могут создавать или редактировать рекламу».
Запрос на запуск отвергается ещё до создания run и задачи, поэтому в Meta не
уходит ни одного запроса: это ``REJECTED`` с причиной словами, а не ``UNKNOWN``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException

from apps.api.routers.v1.campaigns_create import _account_context_rejection
from core.campaign_builder.account_context import (
    CAMPAIGN_ACCOUNT_CONTEXT_STALE,
    CAMPAIGN_ACCOUNT_CONTEXT_UNAVAILABLE,
    CAMPAIGN_ACCOUNT_STATUS_UNKNOWN,
    CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE,
    CampaignAccountContext,
    CampaignAccountContextError,
)


def _context(issue: str, *, state: str = "unavailable", account_status: int | None = None):
    return CampaignAccountContext(
        account_id="123",
        state=state,  # type: ignore[arg-type]
        timezone_name="America/New_York",
        currency="USD",
        currency_exponent=2,
        observed_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        next_start_date=None,
        issue=issue,
        account_status=account_status,
    )


def test_disabled_account_launch_is_a_named_conflict() -> None:
    error = _account_context_rejection(
        CampaignAccountContextError(_context(CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE, account_status=2))
    )

    assert error.status_code == 409
    assert "отключ" in str(error.detail).lower()
    # Ни машинного кода причины, ни номера статуса Meta в карточке оператора.
    assert CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE not in str(error.detail)


def test_unknown_account_status_blocks_launch_instead_of_assuming_active() -> None:
    error = _account_context_rejection(
        CampaignAccountContextError(_context(CAMPAIGN_ACCOUNT_STATUS_UNKNOWN))
    )

    assert error.status_code == 422
    assert "не подтверждён" in str(error.detail)


def test_missing_snapshot_keeps_the_previous_input_semantics() -> None:
    error = _account_context_rejection(
        CampaignAccountContextError(_context(CAMPAIGN_ACCOUNT_CONTEXT_UNAVAILABLE))
    )

    assert error.status_code == 422
    assert isinstance(error, HTTPException)


def test_stale_snapshot_stays_a_conflict_not_an_account_verdict() -> None:
    """Устаревший снимок не выдаётся за отключение кабинета."""

    context = _context(CAMPAIGN_ACCOUNT_CONTEXT_STALE, state="stale")
    error = _account_context_rejection(CampaignAccountContextError(context))

    assert error.status_code == 409
    assert context.blocked_by_account_status is False
    assert "устарел" in str(error.detail)
