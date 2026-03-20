from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class RulePercentages:
    click_share: Decimal = Decimal("0.02")
    lead_share: Decimal = Decimal("0.10")
    registration_share: Decimal = Decimal("0.20")
    no_deposit_spend_share: Decimal = Decimal("0.50")
    no_deposit_spend_audit_top_share: Decimal = Decimal("0.70")
    after_deposit_spend_share: Decimal = Decimal("0.70")
    after_deposit_spend_audit_top_share: Decimal = Decimal("0.90")
