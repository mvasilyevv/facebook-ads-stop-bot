# -*- coding: utf-8 -*-
"""Диагностика качества трафика по CPM и Frequency."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_CPM_NORMAL_LIMIT = Decimal("110")
_CPM_CRITICAL_LIMIT = Decimal("140")


def _to_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _round_decimal(value: Decimal | None, precision: str = "0.01") -> Decimal | None:
    if value is None:
        return None
    return Decimal(value).quantize(Decimal(precision))


@dataclass(slots=True, frozen=True)
class MetricDiagnostic:
    """Диагностика одной метрики для UI и текста причин."""

    status: str
    label: str
    text: str
    bar_percent: int
    value: Decimal | None = None
    baseline: Decimal | None = None
    ratio_percent: Decimal | None = None
    elevated_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class AdQualityDiagnostics:
    """Полная диагностика объявления для UI и контекста причин."""

    cpm: MetricDiagnostic
    frequency: MetricDiagnostic
    summary_text: str

    def as_dict(self) -> dict:
        return {
            "cpm": self.cpm.as_dict(),
            "frequency": self.frequency.as_dict(),
            "summary_text": self.summary_text,
        }


def compute_cpm_baselines_by_offer(
    items: Iterable[T],
    *,
    offer_code_getter: Callable[[T], str | None],
    cpm_getter: Callable[[T], object | None],
    min_points: int = 3,
) -> dict[str, Decimal]:
    """Считает медианный CPM по офферу из текущего среза."""

    grouped: dict[str, list[Decimal]] = {}
    for item in items:
        offer_code = offer_code_getter(item)
        cpm_value = _to_decimal(cpm_getter(item))
        if not offer_code or cpm_value is None or cpm_value <= _ZERO:
            continue
        grouped.setdefault(offer_code, []).append(cpm_value)

    baselines: dict[str, Decimal] = {}
    for offer_code, values in grouped.items():
        if len(values) < min_points:
            continue
        baselines[offer_code] = _round_decimal(Decimal(str(median(values))), "0.0001") or _ZERO
    return baselines


def build_ad_quality_diagnostics(
    *,
    cpm_value: object | None,
    cpm_baseline: Decimal | None,
    frequency_value: object | None,
    frequency_elevated_threshold: object | None,
    frequency_critical_threshold: object | None,
) -> AdQualityDiagnostics:
    """Строит итоговую диагностику объявления по CPM и Frequency."""

    cpm = _build_cpm_diagnostic(cpm_value=cpm_value, cpm_baseline=cpm_baseline)
    frequency = _build_frequency_diagnostic(
        frequency_value=frequency_value,
        elevated_threshold=frequency_elevated_threshold,
        critical_threshold=frequency_critical_threshold,
    )
    summary_text = _build_summary_text(cpm=cpm, frequency=frequency)
    return AdQualityDiagnostics(cpm=cpm, frequency=frequency, summary_text=summary_text)


def build_diagnostics_context_text(diagnostics: AdQualityDiagnostics) -> str | None:
    """Возвращает короткую вторую фразу для причины алерта."""

    parts: list[str] = []

    if diagnostics.cpm.status in {"elevated", "critical"}:
        if diagnostics.cpm.value is not None and diagnostics.cpm.baseline is not None:
            ratio = diagnostics.cpm.ratio_percent or _ZERO
            parts.append(
                f"CPM ${diagnostics.cpm.value:.2f} — это {ratio:.0f}% от медианы оффера ${diagnostics.cpm.baseline:.2f}"
            )

    if diagnostics.frequency.status in {"elevated", "critical"}:
        if (
            diagnostics.frequency.value is not None
            and diagnostics.frequency.critical_threshold is not None
        ):
            parts.append(
                f"частота {diagnostics.frequency.value:.2f} при критической границе {diagnostics.frequency.critical_threshold:.2f}"
            )

    if not parts:
        return None

    return "Дополнительно " + "; ".join(parts) + "."


def _build_cpm_diagnostic(
    *, cpm_value: object | None, cpm_baseline: Decimal | None
) -> MetricDiagnostic:
    value = _to_decimal(cpm_value)
    baseline = _to_decimal(cpm_baseline)

    if value is None or value <= _ZERO:
        return MetricDiagnostic(
            status="insufficient_data",
            label="CPM vs медиана оффера",
            text="Для CPM пока нет данных по самому объявлению.",
            bar_percent=0,
            value=value,
            baseline=baseline,
        )

    if baseline is None or baseline <= _ZERO:
        return MetricDiagnostic(
            status="insufficient_data",
            label="CPM vs медиана оффера",
            text="Для честной оценки CPM пока не хватает минимум трёх активных объявлений этого оффера.",
            bar_percent=0,
            value=_round_decimal(value, "0.0001"),
            baseline=baseline,
        )

    ratio_percent = (value / baseline) * _HUNDRED
    bar_percent = min(100, int((ratio_percent / _CPM_CRITICAL_LIMIT) * _HUNDRED))

    if ratio_percent > _CPM_CRITICAL_LIMIT:
        status = "critical"
        text = (
            f"CPM ${value:.2f}, это {ratio_percent:.0f}% от медианы оффера ${baseline:.2f}. "
            "Аукцион заметно перегрет относительно других активных объявлений."
        )
    elif ratio_percent > _CPM_NORMAL_LIMIT:
        status = "elevated"
        text = (
            f"CPM ${value:.2f}, это {ratio_percent:.0f}% от медианы оффера ${baseline:.2f}. "
            "Трафик покупается дороже обычного для этого оффера."
        )
    else:
        status = "normal"
        text = (
            f"CPM ${value:.2f}, это {ratio_percent:.0f}% от медианы оффера ${baseline:.2f}. "
            "По аукциону явного перегрева не видно."
        )

    return MetricDiagnostic(
        status=status,
        label="CPM vs медиана оффера",
        text=text,
        bar_percent=bar_percent,
        value=_round_decimal(value, "0.0001"),
        baseline=_round_decimal(baseline, "0.0001"),
        ratio_percent=_round_decimal(ratio_percent, "0.01"),
    )


def _build_frequency_diagnostic(
    *,
    frequency_value: object | None,
    elevated_threshold: object | None,
    critical_threshold: object | None,
) -> MetricDiagnostic:
    value = _to_decimal(frequency_value)
    elevated = _to_decimal(elevated_threshold) or Decimal("2")
    critical = _to_decimal(critical_threshold) or Decimal("3")

    if value is None or value <= _ZERO:
        return MetricDiagnostic(
            status="insufficient_data",
            label="Частота vs норма оффера",
            text="Для частоты пока нет данных по этому объявлению.",
            bar_percent=0,
            value=value,
            elevated_threshold=_round_decimal(elevated, "0.01"),
            critical_threshold=_round_decimal(critical, "0.01"),
        )

    bar_percent = min(100, int((value / critical) * _HUNDRED))
    if value >= critical:
        status = "critical"
        text = (
            f"Частота {value:.2f} уже дошла до критической зоны от {critical:.2f}. "
            "Аудитория быстро выгорает, и объявление начнёт терять отклик."
        )
    elif value > elevated:
        status = "elevated"
        text = (
            f"Частота {value:.2f} уже выше рабочей нормы {elevated:.2f}. "
            "Креатив начинает чаще повторяться одной и той же аудитории."
        )
    else:
        status = "normal"
        text = (
            f"Частота {value:.2f} остаётся в пределах рабочей нормы до {elevated:.2f}. "
            "По частоте выгорания явной проблемы пока нет."
        )

    return MetricDiagnostic(
        status=status,
        label="Частота vs норма оффера",
        text=text,
        bar_percent=bar_percent,
        value=_round_decimal(value, "0.0001"),
        elevated_threshold=_round_decimal(elevated, "0.01"),
        critical_threshold=_round_decimal(critical, "0.01"),
    )


def _build_summary_text(*, cpm: MetricDiagnostic, frequency: MetricDiagnostic) -> str:
    if cpm.status == "insufficient_data" and frequency.status == "insufficient_data":
        return "Для CPM и частоты пока недостаточно данных."
    if cpm.status in {"critical", "elevated"} and frequency.status in {"critical", "elevated"}:
        return "И аукцион, и частота показывают ухудшение качества трафика."
    if cpm.status in {"critical", "elevated"}:
        return "Главный риск сейчас в дорогом аукционе по CPM."
    if frequency.status in {"critical", "elevated"}:
        return "Главный риск сейчас в выгорании аудитории по частоте."
    return "CPM и частота не показывают явной вторичной проблемы."
