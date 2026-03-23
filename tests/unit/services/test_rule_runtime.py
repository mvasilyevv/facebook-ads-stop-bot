from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from core.services.rule_runtime import resolve_rule_runtime


@dataclass(slots=True)
class _FakeRule:
    code: str
    is_enabled: bool
    config_json: dict


class _FakeRulesRepository:
    def __init__(self, rules: list[_FakeRule]) -> None:
        self._rules = rules
        self.ensure_calls = 0

    async def ensure_default_rules(self) -> None:
        self.ensure_calls += 1

    async def list_rules(self) -> list[_FakeRule]:
        return self._rules


# Проверяет, что runtime-конфиг правил подхватывает проценты и флаги из репозитория.
@pytest.mark.asyncio
async def test_resolve_rule_runtime_reads_percentages_and_switches_from_repository() -> None:
    repo = _FakeRulesRepository(
        rules=[
            _FakeRule("stop_high_cpc", True, {"priority": 10, "cpa_multiplier": "0.0160"}),
            _FakeRule("stop_high_cpl", True, {"priority": 20, "cpa_multiplier": "0.0950"}),
            _FakeRule("stop_high_cpr", False, {"priority": 30, "cpa_multiplier": "0.1900"}),
            _FakeRule("stop_five_regs_without_deposit", True, {"priority": 40}),
            _FakeRule(
                "stop_spend_window_without_deposit",
                False,
                {"priority": 50, "cpa_multiplier": "0.5000"},
            ),
            _FakeRule("stop_spend_after_deposit", True, {"priority": 60, "cpa_multiplier": "0.70"}),
        ]
    )

    runtime = await resolve_rule_runtime(repo)  # type: ignore[arg-type]

    assert repo.ensure_calls == 1
    assert runtime.percentages.click_share == Decimal("0.0160")
    assert runtime.percentages.lead_share == Decimal("0.0950")
    assert runtime.percentages.registration_share == Decimal("0.1900")
    assert runtime.switches.stop_high_cpc is True
    assert runtime.switches.stop_high_cpr is False
    assert runtime.switches.stop_spend_window_without_deposit is False
