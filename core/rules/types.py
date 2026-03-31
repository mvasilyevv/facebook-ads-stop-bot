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
    reason_text: str


@dataclass(slots=True, frozen=True)
class RuleEvaluation:
    """Результат оценки правил для одного объявления."""

    stage: AlertStage | None
    early_signal_hits: tuple[RuleHit, ...]
    warning_hits: tuple[RuleHit, ...]
    stop_hits: tuple[RuleHit, ...]

    @property
    def early_signal_rule_codes(self) -> list[str]:
        return [hit.code for hit in self.early_signal_hits]

    @property
    def warning_rule_codes(self) -> list[str]:
        return [hit.code for hit in self.warning_hits]

    @property
    def stop_rule_codes(self) -> list[str]:
        return [hit.code for hit in self.stop_hits]

    @property
    def matched_hits(self) -> tuple[RuleHit, ...]:
        if self.stage == AlertStage.STOP:
            return self.stop_hits
        if self.stage == AlertStage.WARNING:
            return self.warning_hits
        if self.stage == AlertStage.EARLY_SIGNAL:
            return self.early_signal_hits
        return ()

    @property
    def matched_rule_codes(self) -> list[str]:
        return [hit.code for hit in self.matched_hits]

    @property
    def reason_title(self) -> str | None:
        return self.matched_hits[0].title if self.matched_hits else None

    @property
    def reason_text(self) -> str | None:
        return self.matched_hits[0].reason_text if self.matched_hits else None


@dataclass(slots=True, frozen=True)
class RuleContext:
    """Контекст правил для одного оффера."""

    cpa_amount: Decimal
    warning_percent_of_stop: Decimal
    stop_percent_of_base: Decimal = Decimal("100")
    cpc_warning_percent_of_stop: Decimal | None = None
    cpc_stop_percent_of_base: Decimal | None = None
    cpl_warning_percent_of_stop: Decimal | None = None
    cpl_stop_percent_of_base: Decimal | None = None
    cpr_warning_percent_of_stop: Decimal | None = None
    cpr_stop_percent_of_base: Decimal | None = None

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

    # Ранний сигнал 1: слабый Outbound CTR
    early_outbound_ctr_signal_enabled: bool = True
    early_outbound_ctr_signal_min_percent: Decimal = Decimal("0.80")
    early_outbound_ctr_signal_min_spend_percent: Decimal = Decimal("5")

    # Ранний сигнал 2: слабый LPV ratio
    early_lpv_ratio_signal_enabled: bool = True
    early_lpv_ratio_signal_min_percent: Decimal = Decimal("60")
    early_lpv_ratio_signal_min_outbound_clicks: int = 5

    # Ранний сигнал 3: дорогой Cost per LPV
    early_cost_per_lpv_signal_enabled: bool = True
    early_cost_per_lpv_signal_percent_of_cpa: Decimal = Decimal("5")
    early_cost_per_lpv_signal_min_views: int = 2

    @property
    def effective_cpc_warning_percent_of_stop(self) -> Decimal:
        return (
            self.cpc_warning_percent_of_stop
            if self.cpc_warning_percent_of_stop is not None
            else self.warning_percent_of_stop
        )

    @property
    def effective_cpc_stop_percent_of_base(self) -> Decimal:
        return (
            self.cpc_stop_percent_of_base
            if self.cpc_stop_percent_of_base is not None
            else self.stop_percent_of_base
        )

    @property
    def effective_cpl_warning_percent_of_stop(self) -> Decimal:
        return (
            self.cpl_warning_percent_of_stop
            if self.cpl_warning_percent_of_stop is not None
            else self.warning_percent_of_stop
        )

    @property
    def effective_cpl_stop_percent_of_base(self) -> Decimal:
        return (
            self.cpl_stop_percent_of_base
            if self.cpl_stop_percent_of_base is not None
            else self.stop_percent_of_base
        )

    @property
    def effective_cpr_warning_percent_of_stop(self) -> Decimal:
        return (
            self.cpr_warning_percent_of_stop
            if self.cpr_warning_percent_of_stop is not None
            else self.warning_percent_of_stop
        )

    @property
    def effective_cpr_stop_percent_of_base(self) -> Decimal:
        return (
            self.cpr_stop_percent_of_base
            if self.cpr_stop_percent_of_base is not None
            else self.stop_percent_of_base
        )
