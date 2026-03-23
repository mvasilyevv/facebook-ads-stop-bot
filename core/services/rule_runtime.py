from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.repositories.rules import RulesRepository
from core.rules.types import RulePercentages, RuleSwitches


@dataclass(slots=True, frozen=True)
class RuleRuntimeConfig:
    percentages: RulePercentages
    switches: RuleSwitches


def _parse_decimal_multiplier(raw_value: object, fallback: Decimal) -> Decimal:
    if raw_value is None:
        return fallback
    try:
        value = Decimal(str(raw_value))
    except Exception:  # noqa: BLE001
        return fallback
    if value <= 0:
        return fallback
    return value


async def resolve_rule_runtime(repo: RulesRepository) -> RuleRuntimeConfig:
    await repo.ensure_default_rules()
    rules = {rule.code: rule for rule in await repo.list_rules()}

    default_percentages = RulePercentages()
    percentages = RulePercentages(
        click_share=_parse_decimal_multiplier(
            rules.get("stop_high_cpc", None).config_json.get("cpa_multiplier")
            if rules.get("stop_high_cpc", None) is not None
            else None,
            default_percentages.click_share,
        ),
        lead_share=_parse_decimal_multiplier(
            rules.get("stop_high_cpl", None).config_json.get("cpa_multiplier")
            if rules.get("stop_high_cpl", None) is not None
            else None,
            default_percentages.lead_share,
        ),
        registration_share=_parse_decimal_multiplier(
            rules.get("stop_high_cpr", None).config_json.get("cpa_multiplier")
            if rules.get("stop_high_cpr", None) is not None
            else None,
            default_percentages.registration_share,
        ),
        no_deposit_spend_share=_parse_decimal_multiplier(
            rules.get("stop_spend_window_without_deposit", None).config_json.get("cpa_multiplier")
            if rules.get("stop_spend_window_without_deposit", None) is not None
            else None,
            default_percentages.no_deposit_spend_share,
        ),
        no_deposit_spend_audit_top_share=default_percentages.no_deposit_spend_audit_top_share,
        after_deposit_spend_share=_parse_decimal_multiplier(
            rules.get("stop_spend_after_deposit", None).config_json.get("cpa_multiplier")
            if rules.get("stop_spend_after_deposit", None) is not None
            else None,
            default_percentages.after_deposit_spend_share,
        ),
        after_deposit_spend_audit_top_share=default_percentages.after_deposit_spend_audit_top_share,
    )
    switches = RuleSwitches(
        stop_high_cpc=rules.get("stop_high_cpc", None).is_enabled
        if rules.get("stop_high_cpc", None) is not None
        else True,
        stop_high_cpl=rules.get("stop_high_cpl", None).is_enabled
        if rules.get("stop_high_cpl", None) is not None
        else True,
        stop_high_cpr=rules.get("stop_high_cpr", None).is_enabled
        if rules.get("stop_high_cpr", None) is not None
        else True,
        stop_five_regs_without_deposit=rules.get("stop_five_regs_without_deposit", None).is_enabled
        if rules.get("stop_five_regs_without_deposit", None) is not None
        else True,
        stop_spend_window_without_deposit=rules.get(
            "stop_spend_window_without_deposit", None
        ).is_enabled
        if rules.get("stop_spend_window_without_deposit", None) is not None
        else True,
        stop_spend_after_deposit=rules.get("stop_spend_after_deposit", None).is_enabled
        if rules.get("stop_spend_after_deposit", None) is not None
        else True,
    )
    return RuleRuntimeConfig(percentages=percentages, switches=switches)
