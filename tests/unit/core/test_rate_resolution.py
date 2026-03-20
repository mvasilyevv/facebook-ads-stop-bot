from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from core.domain import (
    OfferBindingCandidate,
    OfferRateCandidate,
    resolve_offer_binding,
    resolve_offer_rate,
)


# Проверяет, что привязка конкретного объявления имеет приоритет над привязкой адсета.
def test_resolve_offer_binding_prefers_ad_override() -> None:
    binding = resolve_offer_binding(
        ad_id="ad-1",
        adset_scope_key="adset-scope-1",
        bindings=[
            OfferBindingCandidate(
                entity_type="adset",
                entity_id="adset-scope-1",
                offer_id="offer-a",
                priority=1,
            ),
            OfferBindingCandidate(
                entity_type="ad",
                entity_id="ad-1",
                offer_id="offer-b",
                priority=10,
            ),
        ],
    )

    assert binding is not None
    assert binding.offer_id == "offer-b"


# Проверяет, что выбирается версия ставки, действовавшая в момент снимка.
def test_resolve_offer_rate_uses_version_for_snapshot_time() -> None:
    resolved = resolve_offer_rate(
        offer_id="offer-a",
        captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        versions=[
            OfferRateCandidate(
                version_id="v1",
                offer_id="offer-a",
                cpa_usd=Decimal("5.00"),
                effective_from=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
                effective_to=datetime(2026, 3, 15, 0, 0, tzinfo=UTC),
            ),
            OfferRateCandidate(
                version_id="v2",
                offer_id="offer-a",
                cpa_usd=Decimal("6.00"),
                effective_from=datetime(2026, 3, 15, 0, 0, tzinfo=UTC),
            ),
        ],
    )

    assert resolved is not None
    assert resolved.version_id == "v2"
    assert resolved.cpa_usd == Decimal("6.00")


# Проверяет, что при отсутствии активной ставки система возвращает отсутствие результата.
def test_resolve_offer_rate_returns_none_without_matching_version() -> None:
    resolved = resolve_offer_rate(
        offer_id="offer-a",
        captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        versions=[
            OfferRateCandidate(
                version_id="v1",
                offer_id="offer-a",
                cpa_usd=Decimal("5.00"),
                effective_from=datetime(2026, 2, 1, 0, 0, tzinfo=UTC),
                effective_to=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
            )
        ],
    )

    assert resolved is None
