# -*- coding: utf-8 -*-
"""Типы данных для правил: RuleHit, RuleEvaluation, RuleContext."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.domain import AlertStage


@dataclass(slots=True, frozen=True)
class RuleHit:
    """Одно сработавшее правило."""

    code: str
    title: str
    stage: AlertStage
    value: Decimal
    threshold: Decimal
    summary: str


@dataclass(slots=True, frozen=True)
class RuleEvaluation:
    """Результат оценки правил для одного объявления."""

    stage: AlertStage | None
    warning_hits: tuple[RuleHit, ...]
    stop_hits: tuple[RuleHit, ...]

    @property
    def warning_rule_codes(self) -> list[str]:
        return [hit.code for hit in self.warning_hits]

    @property
    def stop_rule_codes(self) -> list[str]:
        return [hit.code for hit in self.stop_hits]


@dataclass(slots=True, frozen=True)
class RuleContext:
    """Контекст правил для одного оффера."""

    cpa_amount: Decimal
    warning_percent_of_stop: Decimal
    stop_percent_of_base: Decimal = Decimal("100")

    # Правило 1: CPC
    cpc_enabled: bool = True
    cpc_percent_stop: Decimal = Decimal("2")

    # Правило 2: CPL
    cpl_enabled: bool = True
    cpl_percent_stop: Decimal = Decimal("10")

    # Правило 3: CPR
    cpr_enabled: bool = True
    cpr_percent_stop: Decimal = Decimal("20")

    # Правило 4: N рег без депов
    regs_no_dep_enabled: bool = True
    regs_no_dep_stop_count: int = 5

    # Правило 5: Расход без депа
    spend_no_dep_enabled: bool = True
    spend_no_dep_from_percent: Decimal = Decimal("50")
    spend_no_dep_to_percent: Decimal = Decimal("70")

    # Правило 6: Расход с депом
    spend_with_dep_enabled: bool = True
    spend_with_dep_from_percent: Decimal = Decimal("70")
    spend_with_dep_to_percent: Decimal = Decimal("90")
