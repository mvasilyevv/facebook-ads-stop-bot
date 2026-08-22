# -*- coding: utf-8 -*-
"""Типы данных для правил: RuleHit, RuleEvaluation, RuleContext."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from core.domain import AlertStage
from core.money import (
    currency_quantum,
    require_currency_exponent,
    require_exact_currency_amount,
)

# ── Базовые проценты стоп-правил (ФИКСИРОВАНЫ, НЕ настраиваются через UI/конфиг) ──
# Это сами правила: какая граница (в % от CPA или штуках) считается стопом для каждой
# ступени воронки. Меняются ТОЛЬКО правкой кода. Пользователь регулирует не их, а
# чувствительность (stop_percent_of_base / warning_percent_of_stop) per-offer.
CPC_PERCENT_OF_CPA = Decimal("2")  # клик: стоп-база = 2% от CPA
CPL_PERCENT_OF_CPA = Decimal("10")  # лид: 10% от CPA
CPR_PERCENT_OF_CPA = Decimal("20")  # рега: 20% от CPA
REGS_NO_DEP_STOP_COUNT = 5  # 5 регистраций без депозитов → стоп
SPEND_NO_DEP_FROM_PERCENT = Decimal("50")  # расход без депа: диапазон 50%…
SPEND_NO_DEP_TO_PERCENT = Decimal("70")  # …70% от CPA
SPEND_WITH_DEP_FROM_PERCENT = Decimal("70")  # расход с депом: 70%…
SPEND_WITH_DEP_TO_PERCENT = Decimal("90")  # …90% от CPA

# Минимальный объём знаменателя отношения (#204). Единственное отношение в стоп-правилах —
# частота (показы / охват), её знаменатель — reach. Ниже этого объёма отношение считается
# НЕИЗВЕСТНЫМ (None): на охвате в десятки человек частота 5 — переходный шум Meta (reach
# отстаёт от показов на старте), а не выгорание аудитории. Неизвестное отношение молчит и
# не перебивает смягчение стопа по депозиту.
# Это НЕ возврат к «ожиданию накопления данных» для денежных правил: цена (CPC/CPL/CPR,
# расход от CPA) остаётся подтверждённым фактом на любом объёме и стопает сразу (решение
# байера, 2f2c8d4e). Значение 100 — оттуда же: столько стоял снятый тем коммитом min_reach.
MIN_RATIO_DENOMINATOR = 100


@dataclass(slots=True, frozen=True)
class RuleHit:
    """Контекст одного правила: срабатывание или прогресс до STOP."""

    code: str
    title: str
    stage: AlertStage | None
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
    nearest_stop: RuleHit | None = None

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

    currency: str
    currency_exponent: int
    cpa_amount: Decimal
    warning_percent_of_stop: Decimal
    stop_percent_of_base: Decimal = Decimal("80")
    cpc_warning_percent_of_stop: Decimal | None = None
    cpc_stop_percent_of_base: Decimal | None = None
    cpl_warning_percent_of_stop: Decimal | None = None
    cpl_stop_percent_of_base: Decimal | None = None
    cpr_warning_percent_of_stop: Decimal | None = None
    cpr_stop_percent_of_base: Decimal | None = None
    spend_no_dep_warning_percent_of_stop: Decimal | None = None
    spend_with_dep_warning_percent_of_stop: Decimal | None = None

    # Адаптивный CPA baseline: rolling median по офферу (если включено)
    use_adaptive_cpa: bool = False
    adaptive_cpa: Decimal | None = None

    # Настраиваемые базовые проценты правил (#260): per-offer, дефолты = исторические константы.
    # null в offer_rules → build_rule_context подставляет константу; здесь всегда не-None.
    cpc_percent_of_cpa: Decimal = CPC_PERCENT_OF_CPA
    cpl_percent_of_cpa: Decimal = CPL_PERCENT_OF_CPA
    cpr_percent_of_cpa: Decimal = CPR_PERCENT_OF_CPA

    # Правило 1: CPC — базовый процент вычисляется из cpc_percent_of_cpa в __post_init__.
    # init=False сохранён: нельзя передать напрямую в конструктор (поведение не меняется).
    cpc_enabled: bool = True
    cpc_percent_stop: Decimal = field(init=False)

    # Правило 2: CPL
    cpl_enabled: bool = True
    cpl_percent_stop: Decimal = field(init=False)

    # Правило 3: CPR
    cpr_enabled: bool = True
    cpr_percent_stop: Decimal = field(init=False)

    # Правило 4: N рег без депов — настраивается per-offer (#260).
    regs_no_dep_enabled: bool = True
    regs_no_dep_stop_count: int = REGS_NO_DEP_STOP_COUNT

    # Правило 5: Расход без депа — диапазон настраивается per-offer (#260).
    spend_no_dep_enabled: bool = True
    spend_no_dep_from_percent: Decimal = SPEND_NO_DEP_FROM_PERCENT
    spend_no_dep_to_percent: Decimal = SPEND_NO_DEP_TO_PERCENT

    # Правило 6: Расход с депом — диапазон настраивается per-offer (#260).
    spend_with_dep_enabled: bool = True
    spend_with_dep_from_percent: Decimal = SPEND_WITH_DEP_FROM_PERCENT
    spend_with_dep_to_percent: Decimal = SPEND_WITH_DEP_TO_PERCENT

    # Правило 7: frequency-anomaly (выгорание аудитории). Абсолютные пороги, без истории
    # роста за час — LOW (аудит 02.07): frequency_1h_ago/frequency_growth_warning_pct
    # удалены как мёртвые поля (build_rule_context никогда их не заполнял, ветка в
    # evaluator._evaluate_frequency_anomaly была недостижима).
    frequency_anomaly_enabled: bool = True
    frequency_current: Decimal | None = None
    frequency_warning_threshold: Decimal = Decimal("2.5")
    frequency_stop_threshold: Decimal = Decimal("3.5")
    # impressions — данные строки (диагностика/будущие правила). Гейт-минимум по показам
    # УБРАН (решение байера: стопать жёстко по порогу, не ждать накопления показов —
    # перекрут вреднее статистической нерепрезентативности).
    # reach — знаменатель частоты, и для НЕЁ объём проверяется: ниже
    # MIN_RATIO_DENOMINATOR отношение неизвестно (#204). Это не ожидание данных перед
    # стопом по цене, а отказ считать известным отношение, которое ничего не измеряет.
    # None означает неподтверждённый объём и тоже делает отношение неизвестным.
    impressions: int | None = None
    reach: int | None = None
    # Потолок-выброс частоты: FB на старте может показывать frequency 50-100 из-за
    # крошечного reach (300 показов / 7 человек) — отсекаем только абсурдные выбросы
    # выше cap. Это НЕ «ожидание» данных, а защита от переходного шума.
    frequency_outlier_cap: Decimal = Decimal("10.0")

    # Минимальный объём знаменателя отношения (#260, per-offer). Частота = показы/охват;
    # знаменатель ниже этого порога → отношение неизвестно (молчим, не стопаем по frequency).
    min_ratio_denominator: int = MIN_RATIO_DENOMINATOR

    # Внешние депозиты — от трекера AdSet.pro (см. core.adset_pro.ingest).
    # ЕДИНСТВЕННЫЙ источник истины по депозитам для правил (решение пользователя):
    # Meta-видимые row.deposits в deposit-логике НЕ учитываются. При external_deposits >= 1
    # объявление переходит в deposit_stage и не попадает под no-dep guardrail.
    # Значение — ever-had count (без временного окна): объявление, однажды принёсшее
    # депозит, навсегда остаётся на ветке «с депозитом» и не возвращается под no-dep
    # guardrails на границе кабинетных суток.
    external_deposits: int = 0

    # Счётчик депозитов за СЕГОДНЯ (текущее кабинетное окно) — только для отображения
    # в тексте алерта. Не влияет на ветку (deposit/no-dep): ветка определяется
    # external_deposits (ever-had). None — нет данных от трекера (ещё не было постбэков
    # для этого ad); 0 — подтверждённый ноль за текущий день; >0 — есть сегодня.
    today_deposits: int | None = None

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
    money_quantum: Decimal = field(init=False)

    def __post_init__(self) -> None:
        """Предвычисляет денежные пороги один раз при создании контекста."""
        currency, exponent = require_currency_exponent(
            self.currency,
            self.currency_exponent,
        )
        cpa_amount = require_exact_currency_amount(
            self.cpa_amount,
            currency=currency,
            exponent=exponent,
            field="cpa_amount",
            allow_zero=False,
        )
        step = currency_quantum(currency, exponent)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "currency_exponent", exponent)
        object.__setattr__(self, "cpa_amount", cpa_amount)
        object.__setattr__(self, "money_quantum", step)

        # Переносим настраиваемые базовые проценты в init=False поля-процентов (#260).
        # Это позволяет сохранить внешний контракт (cpc_percent_stop нельзя передать
        # напрямую в конструктор), но читать значение из настроек оффера.
        object.__setattr__(self, "cpc_percent_stop", self.cpc_percent_of_cpa)
        object.__setattr__(self, "cpl_percent_stop", self.cpl_percent_of_cpa)
        object.__setattr__(self, "cpr_percent_stop", self.cpr_percent_of_cpa)

        def _base(percent: Decimal) -> Decimal:
            return (cpa_amount * percent / Decimal("100")).quantize(step, rounding=ROUND_HALF_UP)

        def _stop(base: Decimal, pct_of_base: Decimal) -> Decimal:
            return (base * pct_of_base / Decimal("100")).quantize(step, rounding=ROUND_HALF_UP)

        def _warn(stop: Decimal, warn_pct: Decimal) -> Decimal:
            return (stop * warn_pct / Decimal("100")).quantize(step, rounding=ROUND_HALF_UP)

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

    @property
    def effective_spend_no_dep_warning_percent_of_stop(self) -> Decimal:
        return (
            self.spend_no_dep_warning_percent_of_stop
            if self.spend_no_dep_warning_percent_of_stop is not None
            else self.warning_percent_of_stop
        )

    @property
    def effective_spend_with_dep_warning_percent_of_stop(self) -> Decimal:
        return (
            self.spend_with_dep_warning_percent_of_stop
            if self.spend_with_dep_warning_percent_of_stop is not None
            else self.warning_percent_of_stop
        )
