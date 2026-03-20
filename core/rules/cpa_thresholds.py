from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from core.rules.types import RulePercentages


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(slots=True, frozen=True)
class ThresholdPack:
    cpc_stop: Decimal
    cpl_stop: Decimal
    registration_stop: Decimal
    no_deposit_spend_stop: Decimal
    no_deposit_spend_audit_top: Decimal
    after_deposit_spend_stop: Decimal
    after_deposit_spend_audit_top: Decimal


def build_threshold_pack(
    cpa_amount: Decimal,
    percentages: RulePercentages | None = None,
) -> ThresholdPack:
    """Строит денежные пороги на основе текущего CPA."""

    if cpa_amount <= 0:
        raise ValueError("CPA должна быть больше нуля")

    shares = percentages or RulePercentages()
    return ThresholdPack(
        cpc_stop=_quantize(cpa_amount * shares.click_share),
        cpl_stop=_quantize(cpa_amount * shares.lead_share),
        registration_stop=_quantize(cpa_amount * shares.registration_share),
        no_deposit_spend_stop=_quantize(cpa_amount * shares.no_deposit_spend_share),
        no_deposit_spend_audit_top=_quantize(cpa_amount * shares.no_deposit_spend_audit_top_share),
        after_deposit_spend_stop=_quantize(cpa_amount * shares.after_deposit_spend_share),
        after_deposit_spend_audit_top=_quantize(
            cpa_amount * shares.after_deposit_spend_audit_top_share
        ),
    )
