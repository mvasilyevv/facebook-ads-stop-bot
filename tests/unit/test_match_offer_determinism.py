# -*- coding: utf-8 -*-
"""Unit-тесты детерминизма match_offer_for_ad при равной длине кодов."""

from __future__ import annotations

import uuid
from decimal import Decimal

from core.observer.queries import OfferRules, match_offer_for_ad


def _offer(code: str) -> OfferRules:
    return OfferRules(
        offer_id=uuid.uuid4(),
        code=code,
        name=f"Offer {code}",
        spend_no_event_threshold=Decimal("10"),
        cpa_threshold=Decimal("5"),
        cpm_threshold=Decimal("1"),
        ctr_threshold=Decimal("0.5"),
        frequency_threshold=Decimal("2"),
        funnel_ratio_threshold=Decimal("3"),
    )


# При равной длине алфавитно первый код выигрывает — детерминированно.
def test_equal_length_picks_alphabetical_first() -> None:
    offers = [_offer("CR2"), _offer("CR1")]
    matched = match_offer_for_ad(
        campaign_name="CR1 | CR2 | DE | XYZ",
        ad_name="",
        offers=offers,
    )
    assert matched is not None
    assert matched.code == "CR1"


# Тот же тест с обратным порядком списка — результат должен совпасть.
def test_equal_length_stable_across_input_order() -> None:
    offers_a = [_offer("CR1"), _offer("CR2")]
    offers_b = [_offer("CR2"), _offer("CR1")]
    matched_a = match_offer_for_ad(
        campaign_name="CR1 | CR2 | DE | XYZ", ad_name="", offers=offers_a
    )
    matched_b = match_offer_for_ad(
        campaign_name="CR1 | CR2 | DE | XYZ", ad_name="", offers=offers_b
    )
    assert matched_a is not None and matched_b is not None
    assert matched_a.code == matched_b.code == "CR1"


# Длиннее всегда побеждает — даже если алфавитно «больше».
def test_longer_wins_over_shorter_alphabetically_earlier() -> None:
    offers = [_offer("CR2_LONG"), _offer("AAA")]
    matched = match_offer_for_ad(
        campaign_name="CR2_LONG | AAA | DE",
        ad_name="",
        offers=offers,
    )
    assert matched is not None
    assert matched.code == "CR2_LONG"


# Повторные вызовы возвращают тот же результат (детерминизм).
def test_repeated_calls_return_same_result() -> None:
    offers = [_offer("AB"), _offer("CD"), _offer("EF")]
    text = "AB | CD | EF | DE"
    first = match_offer_for_ad(campaign_name=text, ad_name="", offers=offers)
    second = match_offer_for_ad(campaign_name=text, ad_name="", offers=offers)
    third = match_offer_for_ad(campaign_name=text, ad_name="", offers=list(reversed(offers)))
    assert first is not None
    assert first.code == second.code == third.code == "AB"


# ad_name по-прежнему побеждает campaign_name (приоритет источника не задет).
def test_ad_name_priority_preserved() -> None:
    offers = [_offer("AA"), _offer("BB")]
    matched = match_offer_for_ad(
        campaign_name="BB | DE | XYZ",
        ad_name="AA-creative",
        offers=offers,
    )
    assert matched is not None
    assert matched.code == "AA"
