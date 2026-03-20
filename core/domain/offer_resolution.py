from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class OfferBindingCandidate:
    entity_type: str
    entity_id: str
    offer_id: str
    priority: int
    is_active: bool = True


@dataclass(slots=True, frozen=True)
class OfferRateCandidate:
    version_id: str
    offer_id: str
    cpa_usd: Decimal
    effective_from: datetime
    effective_to: datetime | None = None


@dataclass(slots=True, frozen=True)
class ResolvedOfferRate:
    offer_id: str
    version_id: str
    cpa_usd: Decimal


def resolve_offer_binding(
    ad_id: str | None,
    adset_scope_key: str | None,
    bindings: list[OfferBindingCandidate],
) -> OfferBindingCandidate | None:
    """Возвращает наиболее приоритетную активную привязку оффера."""

    candidates = [binding for binding in bindings if binding.is_active]

    if ad_id is not None:
        ad_matches = [
            item for item in candidates if item.entity_type == "ad" and item.entity_id == ad_id
        ]
        if ad_matches:
            return sorted(ad_matches, key=lambda item: item.priority, reverse=True)[0]

    if adset_scope_key is not None:
        adset_matches = [
            item
            for item in candidates
            if item.entity_type == "adset" and item.entity_id == adset_scope_key
        ]
        if adset_matches:
            return sorted(adset_matches, key=lambda item: item.priority, reverse=True)[0]

    return None


def resolve_offer_rate(
    offer_id: str,
    captured_at: datetime,
    versions: list[OfferRateCandidate],
) -> ResolvedOfferRate | None:
    """Возвращает ставку оффера, действовавшую в момент снимка."""

    applicable = [
        version
        for version in versions
        if version.offer_id == offer_id
        and version.effective_from <= captured_at
        and (version.effective_to is None or captured_at < version.effective_to)
    ]
    if not applicable:
        return None

    selected = sorted(applicable, key=lambda item: item.effective_from, reverse=True)[0]
    return ResolvedOfferRate(
        offer_id=selected.offer_id,
        version_id=selected.version_id,
        cpa_usd=selected.cpa_usd,
    )
