# -*- coding: utf-8 -*-
"""Unit: data-driven расчёт порога frequency-anomaly (core/rules/frequency_analyzer.py, #37).

Проверяем чистую функцию compute_frequency_threshold на синтетике: обнаружение
деградации, отсутствие сигнала, нехватку данных, устойчивость медианы к выбросам,
clamp и граничные случаи. БД не нужна.
"""

from __future__ import annotations

from decimal import Decimal

from core.rules.frequency_analyzer import (
    FrequencyThresholdConfig,
    _bucket_floor,
    compute_frequency_threshold,
)


def _pts(*groups: tuple[float, float, int]) -> list[tuple[Decimal, Decimal]]:
    """(frequency, metric, count) → развёрнутый список точек."""
    out: list[tuple[Decimal, Decimal]] = []
    for freq, metric, count in groups:
        out.extend([(Decimal(str(freq)), Decimal(str(metric)))] * count)
    return out


# _bucket_floor: floor частоты к кратному шага
def test_bucket_floor() -> None:
    assert _bucket_floor(Decimal("3.7"), Decimal("0.5")) == Decimal("3.50")
    assert _bucket_floor(Decimal("3.5"), Decimal("0.5")) == Decimal("3.50")
    assert _bucket_floor(Decimal("2.0"), Decimal("0.5")) == Decimal("2.00")
    assert _bucket_floor(Decimal("1.2"), Decimal("0.5")) == Decimal("1.00")


# baseline дёшево (metric=10), на частоте >=3.0 метрика выросла до 15 (>13=10*1.3) → порог 3.0
def test_detects_degradation() -> None:
    points = _pts(
        (1.5, 10, 20),  # baseline (freq < 2.0)
        (2.2, 11, 10),  # +10% — ниже порога деградации (13)
        (3.2, 15, 10),  # +50% — деградация
    )
    res = compute_frequency_threshold(points)
    assert res.threshold == Decimal("3.00")
    assert res.baseline_metric == Decimal("10.00")


# Метрика стабильна на всех частотах → деградации нет → порог не выставляем
def test_no_degradation_returns_none() -> None:
    points = _pts((1.5, 10, 20), (2.5, 10, 10), (3.5, 10, 10))
    res = compute_frequency_threshold(points)
    assert res.threshold is None
    assert "не обнаружено" in res.reason


# Точек меньше min_total_samples → None
def test_insufficient_data() -> None:
    points = _pts((1.5, 10, 5), (3.0, 50, 3))
    res = compute_frequency_threshold(points)
    assert res.threshold is None
    assert "недостаточно данных" in res.reason


# Нет точек на низкой частоте → baseline не построить → None
def test_no_baseline() -> None:
    points = _pts((2.5, 10, 20), (3.5, 50, 20))
    res = compute_frequency_threshold(points)
    assert res.threshold is None
    assert "baseline" in res.reason


# Один экстремальный выброс в бакете НЕ сдвигает медиану → ложного порога нет
def test_median_robust_to_outliers() -> None:
    points = _pts(
        (1.5, 10, 20),  # baseline
        (3.0, 11, 9),  # медиана бакета 3.0 = 11 (<13)
        (3.0, 1000, 1),  # выброс — на медиану не влияет
    )
    res = compute_frequency_threshold(points)
    assert res.threshold is None  # median 11 < 13, выброс проигнорирован


# Деградация на очень высокой частоте → порог зажат max_threshold
def test_clamp_to_max_threshold() -> None:
    points = _pts((1.5, 10, 20), (12.0, 30, 10))
    res = compute_frequency_threshold(points)
    assert res.threshold == Decimal("10.00")  # min(12.0, max_threshold=10)


# baseline-метрика = 0 → деградацию не от чего считать → None
def test_baseline_zero_returns_none() -> None:
    points = _pts((1.5, 0, 20), (3.0, 5, 10))
    res = compute_frequency_threshold(points)
    assert res.threshold is None
    assert "baseline" in res.reason.lower()


# Бакет с деградацией, но точек < min_samples_per_bucket → пропускается (ненадёжно)
def test_thin_bucket_skipped() -> None:
    # baseline 20т; бакет 3.0 деградировал, но всего 3 точки (<5) → не порог
    points = _pts((1.5, 10, 20), (3.0, 50, 3), (1.7, 10, 10))
    res = compute_frequency_threshold(points)
    assert res.threshold is None


# Кастомный config: degradation_pct=50 поднимает планку срабатывания
def test_custom_degradation_pct() -> None:
    points = _pts((1.5, 10, 20), (3.0, 13, 10))  # +30%
    # При дефолте (30%) сработало бы; при 50% (порог 15) — нет.
    res = compute_frequency_threshold(
        points, FrequencyThresholdConfig(degradation_pct=Decimal("50"))
    )
    assert res.threshold is None
