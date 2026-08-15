# -*- coding: utf-8 -*-
"""Money-инварианты fan-out запуска кампаний без живой БД."""

from __future__ import annotations

import pytest

from apps.api.routers.v1.campaigns_create import (
    _compute_idempotency_key,
    _run_account_launches_independently,
)
from core.campaign_builder.config import (
    Account,
    AdsetConfig,
    Budget,
    CampaignBlock,
    CampaignConfig,
    Targeting,
)


def _config(account_id: str) -> CampaignConfig:
    return CampaignConfig(
        account=Account(
            act_id=account_id,
            page_id="111",
            pixel_id="222",
            timezone_name="Etc/UTC",
            currency="USD",
            account_context_observed_at="2026-08-15T10:00:00+00:00",
        ),
        offer_code="GH_CR",
        destination_link="https://example.test/click",
        start_date="2099-08-16",
        creo_root="upload-1",
        budget=Budget(currency="USD", daily_amount="10.00", bid_amount="5.00"),
        targeting=Targeting(countries=["GH"]),
        campaigns=[
            CampaignBlock(
                key="static",
                name="{offer}",
                adsets=[AdsetConfig(name="{offer}", dir="static", glob="*.jpg")],
                concept_refs=["creative.jpg"],
            )
        ],
    )


def test_idempotency_key_is_scoped_by_account() -> None:
    """Одинаковый план в двух кабинетах не склеивается в один money-run."""

    assert _compute_idempotency_key(_config("111")) != _compute_idempotency_key(_config("222"))


@pytest.mark.asyncio
async def test_account_failure_does_not_cancel_later_accounts() -> None:
    """Ошибка одного кабинета не прерывает независимый запуск остальных."""

    called: list[str] = []

    async def launch_one(account_id: str) -> str:
        called.append(account_id)
        if account_id == "222":
            raise ValueError("контекст кабинета не подтверждён")
        return f"run-{account_id}"

    results = await _run_account_launches_independently(
        ("111", "222", "333"),
        launch_one,
    )

    assert called == ["111", "222", "333"]
    assert [(result.account_id, result.value) for result in results if result.error is None] == [
        ("111", "run-111"),
        ("333", "run-333"),
    ]
    assert [(result.account_id, str(result.error)) for result in results if result.error] == [
        ("222", "контекст кабинета не подтверждён")
    ]


@pytest.mark.asyncio
async def test_repeated_accounts_are_not_executed_twice_inside_one_request() -> None:
    """Дедуп выбранных кабинетов не создаёт две попытки одного account slice."""

    calls: list[str] = []

    async def launch_one(account_id: str) -> str:
        calls.append(account_id)
        return account_id

    results = await _run_account_launches_independently(("111", "111", "222"), launch_one)

    assert calls == ["111", "222"]
    assert [result.account_id for result in results] == ["111", "222"]
