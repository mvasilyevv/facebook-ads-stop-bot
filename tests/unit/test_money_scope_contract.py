from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.api.routers.postback import _normalize
from apps.api.routers.v1.schemas.offers import OfferRuleUpsertIn
from core.meta_api import account_tz
from core.meta_api.account_tz import (
    AccountCurrencyResolution,
    CabinetCurrencyUnknownError,
    currency_evidence_is_fresh,
)
from core.meta_api.schemas import MetaMutationPayload
from core.money import (
    CurrencyExponentMismatchError,
    InvalidCurrencyAmountError,
    currency_exponent,
    require_currency_exponent,
    require_exact_currency_amount,
    validated_currency_code,
)


def test_currency_validation_rejects_arbitrary_three_letter_code() -> None:
    assert validated_currency_code("usd") == "USD"
    assert validated_currency_code("KES") == "KES"
    assert validated_currency_code("ZZZ") is None
    assert validated_currency_code("XXX") is None

    with pytest.raises(ValueError, match="unknown ISO 4217"):
        OfferRuleUpsertIn(cpa_threshold=Decimal("3"), currency="ZZZ")


def test_reviewed_currency_exponents_have_no_two_decimal_fallback() -> None:
    assert currency_exponent("JPY") == 0
    assert currency_exponent("USD") == 2
    assert currency_exponent("KWD") == 3

    with pytest.raises(ValueError, match="reviewed exponent"):
        currency_exponent("XAU")
    with pytest.raises(CurrencyExponentMismatchError):
        require_currency_exponent("KWD", 2)
    with pytest.raises(InvalidCurrencyAmountError, match="excess precision"):
        require_exact_currency_amount(
            Decimal("1.001"),
            currency="JPY",
            exponent=0,
        )


def test_offer_cpa_requires_exact_reviewed_currency_precision() -> None:
    with pytest.raises(ValueError, match="excess precision"):
        OfferRuleUpsertIn(cpa_threshold=Decimal("3.1"), currency="JPY")
    with pytest.raises(ValueError, match="reviewed exponent"):
        OfferRuleUpsertIn(cpa_threshold=Decimal("3"), currency="XAU")

    kwd = OfferRuleUpsertIn(cpa_threshold=Decimal("3.125"), currency="KWD")
    assert kwd.cpa_threshold == Decimal("3.125")


def test_postback_without_currency_keeps_money_unit_unknown() -> None:
    received_at = datetime(2026, 7, 29, 12, tzinfo=UTC)
    event, reason = _normalize(
        {
            "event_type": "ftd",
            "click_id": "click-1",
            "revenue": "12.50",
        },
        received_at=received_at,
    )

    assert reason == "accepted"
    assert event is not None
    assert event.revenue == Decimal("12.50")
    assert event.currency is None


def test_currency_scope_uses_oldest_observation_and_distinguishes_mixed() -> None:
    oldest = datetime(2026, 7, 29, 8, tzinfo=UTC)
    newest = datetime(2026, 7, 29, 9, tzinfo=UTC)
    resolution = AccountCurrencyResolution(
        account_ids=("1", "2"),
        currencies={"1": "USD", "2": "EUR"},
        observed_at_by_account={"1": newest, "2": oldest},
        missing_account_ids=(),
    )

    assert resolution.state == "mixed"
    assert resolution.currency is None
    assert resolution.observed_at == oldest


def test_stale_currency_evidence_is_not_authoritative() -> None:
    now = datetime(2026, 7, 29, 12, tzinfo=UTC)

    assert currency_evidence_is_fresh(now - timedelta(hours=23), now=now)
    assert not currency_evidence_is_fresh(now - timedelta(hours=25), now=now)
    assert not currency_evidence_is_fresh(now + timedelta(minutes=6), now=now)


@pytest.mark.asyncio
async def test_required_currency_blocks_unknown_or_stale_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stale_resolution(*args, **kwargs) -> AccountCurrencyResolution:
        del args, kwargs
        return AccountCurrencyResolution(
            account_ids=("123",),
            currencies={},
            observed_at_by_account={},
            missing_account_ids=("123",),
        )

    monkeypatch.setattr(account_tz, "resolve_account_currencies", stale_resolution)

    with pytest.raises(CabinetCurrencyUnknownError):
        await account_tz.resolve_required_account_currency(
            object(),  # type: ignore[arg-type]
            account_id="123",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("database_currency", ["ZZZ", "XAU"])
async def test_resolver_rejects_currency_without_reviewed_exponent(
    database_currency: str,
) -> None:
    class _Result:
        def mappings(self):
            return iter(
                (
                    {
                        "account_id": "123",
                        "currency": database_currency,
                        "currency_observed_at": datetime(2026, 7, 29, 12, tzinfo=UTC),
                    },
                )
            )

    class _Connection:
        async def execute(self, *args, **kwargs):
            del args, kwargs
            return _Result()

    class _ConnectionContext:
        async def __aenter__(self):
            return _Connection()

        async def __aexit__(self, *args):
            del args

    class _Engine:
        def connect(self):
            return _ConnectionContext()

    engine = _Engine()
    now = datetime(2026, 7, 29, 12, 1, tzinfo=UTC)
    resolution = await account_tz.resolve_account_currencies(
        engine,  # type: ignore[arg-type]
        account_ids=["123"],
        now=now,
    )

    assert resolution.state == "unknown"
    assert resolution.currency is None
    assert resolution.missing_account_ids == ("123",)
    with pytest.raises(CabinetCurrencyUnknownError):
        await account_tz.resolve_required_account_currency(
            engine,  # type: ignore[arg-type]
            account_id="123",
            now=now,
        )


def test_action_payload_round_trips_immutable_account_context() -> None:
    payload = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id="ad-1",
        ad_account_id="123",
        currency="USD",
        cabinet_timezone="Europe/Kaliningrad",
        account_context_observed_at="2026-07-29T12:00:00+00:00",
        account_context_issues=(),
    )

    serialized = payload.to_dict()
    assert serialized["account_id"] == "123"
    assert serialized["currency"] == "USD"
    assert serialized["cabinet_timezone"] == "Europe/Kaliningrad"
    assert MetaMutationPayload.from_dict(serialized) == payload
