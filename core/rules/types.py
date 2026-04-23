# -*- coding: utf-8 -*-
"""Типы данных для правил: RuleHit, RuleEvaluation, RuleContext."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

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
    warning_hits: tuple[RuleHit, ...]
    stop_hits: tuple[RuleHit, ...]

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

    # Предвычисленные пороги (init=False — заполняются в __post_init__)
    cpc_base_stop_threshold: Decimal = field(init=False)
    cpc_stop_threshold: Decimal = field(init=False)
    cpc_warning_threshold: Decimal = field(init=False)
    cpl_base_stop_threshold: Decimal = field(init=False)
    cpl_stop_threshold: Decimal = field(init=False)
    cpl_warning_threshold: Decimal = field(init=False)
    cpr_base_stop_threshold: Decimal = field(init=False)
    cpr_stop_threshold: Decimal = field(init=False)
    cpr_warning_threshold: Decimal = field(init=False)

    def __post_init__(self) -> None:
        """Предвычисляет денежные пороги один раз при создании контекста."""
        _step = Decimal("0.01")

        def _base(percent: Decimal) -> Decimal:
            return (self.cpa_amount * percent / Decimal("100")).quantize(
                _step, rounding=ROUND_HALF_UP
            )

        def _stop(base: Decimal, pct_of_base: Decimal) -> Decimal:
            return (base * pct_of_base / Decimal("100")).quantize(_step, rounding=ROUND_HALF_UP)

        def _warn(stop: Decimal, warn_pct: Decimal) -> Decimal:
            return (stop * warn_pct / Decimal("100")).quantize(_step, rounding=ROUND_HALF_UP)

        cpc_base = _base(self.cpc_percent_stop)
        cpc_stop = _stop(cpc_base, self.effective_cpc_stop_percent_of_base)
        object.__setattr__(self, "cpc_base_stop_threshold", cpc_base)
        object.__setattr__(self, "cpc_stop_threshold", cpc_stop)
        object.__setattr__(
            self,
            "cpc_warning_threshold",
            _warn(cpc_stop, self.effective_cpc_warning_percent_of_stop),
        )

        cpl_base = _base(self.cpl_percent_stop)
        cpl_stop = _stop(cpl_base, self.effective_cpl_stop_percent_of_base)
        object.__setattr__(self, "cpl_base_stop_threshold", cpl_base)
        object.__setattr__(self, "cpl_stop_threshold", cpl_stop)
        object.__setattr__(
            self,
            "cpl_warning_threshold",
            _warn(cpl_stop, self.effective_cpl_warning_percent_of_stop),
        )

        cpr_base = _base(self.cpr_percent_stop)
        cpr_stop = _stop(cpr_base, self.effective_cpr_stop_percent_of_base)
        object.__setattr__(self, "cpr_base_stop_threshold", cpr_base)
        object.__setattr__(self, "cpr_stop_threshold", cpr_stop)
        object.__setattr__(
            self,
            "cpr_warning_threshold",
            _warn(cpr_stop, self.effective_cpr_warning_percent_of_stop),
        )

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
