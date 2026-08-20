# -*- coding: utf-8 -*-
"""Инвариант: кабинет, не подтверждённый активным, никогда не выглядит готовым.

20.08.2026 залив вставал на отказе Meta «Отключенные аккаунты не могут создавать
или редактировать рекламу», а контекст того же кабинета отдавал ``ready`` и
``issue: null`` — оператор видел зелёное и запускал заведомо обречённый залив.

Решение проверяется здесь на чистой функции: строка снимка → состояние контекста.
Живая БД для этого не нужна, а инвариант ловится ровно там, где он живёт.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.campaign_builder.account_context import (
    CAMPAIGN_ACCOUNT_CONTEXT_STALE,
    CAMPAIGN_ACCOUNT_CONTEXT_UNAVAILABLE,
    CAMPAIGN_ACCOUNT_STATUS_UNKNOWN,
    CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE,
    campaign_account_context_from_row,
    campaign_account_context_message,
)

_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _row(**overrides: object) -> dict[str, object]:
    """Снимок полностью подтверждённого активного кабинета."""

    row: dict[str, object] = {
        "timezone_name": "America/New_York",
        "currency": "USD",
        "currency_observed_at": _NOW - timedelta(hours=1),
        "account_status": 1,
        "account_status_observed_at": _NOW - timedelta(hours=1),
    }
    row.update(overrides)
    return row


def _context(**overrides: object):
    return campaign_account_context_from_row(
        _row(**overrides),
        account_id="123",
        now=_NOW,
    )


def test_confirmed_active_account_is_ready() -> None:
    context = _context()

    assert context.state == "ready"
    assert context.issue is None
    assert context.account_status == 1
    assert campaign_account_context_message(context) is None


@pytest.mark.parametrize("status", [2, 3, 7, 8, 9, 100, 101])
def test_account_meta_disabled_is_never_ready(status: int) -> None:
    """Любой неактивный статус — недоступность, а не готовность."""

    context = _context(account_status=status)

    assert context.state == "unavailable"
    assert context.issue == CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE
    assert context.next_start_date is None


def test_disabled_account_names_the_reason_in_operator_language() -> None:
    context = _context(account_status=2)
    message = campaign_account_context_message(context)

    assert message is not None
    assert "отключ" in message.lower()
    # Машинный код и номер статуса остаются в логе, а не в карточке оператора.
    assert CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE not in message
    assert "2" not in message


def test_unknown_status_code_still_blocks_with_a_readable_reason() -> None:
    """Незнакомый код Meta — не повод считать кабинет активным."""

    context = _context(account_status=777)

    assert context.state == "unavailable"
    assert context.issue == CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE
    assert campaign_account_context_message(context)


@pytest.mark.parametrize("raw", [None, "", "активен", 0, -1, True])
def test_missing_status_stays_unknown_and_never_defaults_to_active(raw: object) -> None:
    """``null`` — это «неизвестно», а не «активен»."""

    context = _context(account_status=raw, account_status_observed_at=None)

    assert context.state == "unavailable"
    assert context.issue == CAMPAIGN_ACCOUNT_STATUS_UNKNOWN
    assert context.account_status is None
    assert campaign_account_context_message(context)


def test_status_observation_older_than_evidence_window_is_stale() -> None:
    context = _context(account_status_observed_at=_NOW - timedelta(days=2))

    assert context.state == "stale"
    assert context.issue == CAMPAIGN_ACCOUNT_CONTEXT_STALE


def test_disabled_account_outranks_a_missing_currency_snapshot() -> None:
    """Отключение кабинета — самый конкретный факт, его и называем первым."""

    context = _context(currency=None, currency_observed_at=None, account_status=2)

    assert context.issue == CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE


def test_absent_snapshot_row_reports_missing_context() -> None:
    context = campaign_account_context_from_row(None, account_id="123", now=_NOW)

    assert context.state == "unavailable"
    assert context.issue == CAMPAIGN_ACCOUNT_CONTEXT_UNAVAILABLE
    assert context.account_status is None
