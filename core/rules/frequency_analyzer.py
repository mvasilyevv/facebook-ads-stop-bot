# -*- coding: utf-8 -*-
"""Data-driven вычисление порога frequency-anomaly (правило 7, #37).

Идея: порог выгорания у каждого оффера/аудитории свой. Вместо ручного «пальцем
в небо» считаем его из истории ``ad_metrics`` — ищем частоту, при которой экономика
объявления деградирует относительно «здоровой» доставки на низкой частоте.

Алгоритм (``compute_frequency_threshold``):
1. Бьём точки (frequency, метрика) по бакетам частоты с шагом ``bucket_step``.
2. baseline = медиана метрики на низкой частоте (бакеты < ``baseline_max_frequency``).
3. degraded = baseline × (1 + ``degradation_pct``/100).
4. Идём по бакетам вверх от ``baseline_max_frequency``; первый бакет, где медиана
   метрики ≥ degraded И в нём ≥ ``min_samples_per_bucket`` точек → его нижняя граница
   частоты = порог (clamp в [min_threshold, max_threshold]).
5. Мало данных / нет деградации / нет baseline → порог не определён (None).

Метрика — «чем выше, тем хуже» (по умолчанию ``cost_per_result``): при выгорании
стоимость результата растёт. Медиана (а не среднее) устойчива к выбросам.

ВАЖНО (money): порог управляет авто-стопом. ``apply_recommended_threshold`` по
умолчанию ``dry_run=True`` (только считает, не пишет), а при записи трогает оффер
ТОЛЬКО если ``frequency_threshold IS NULL`` — ручные значения не затираются.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_STEP = Decimal("0.01")


@dataclass(frozen=True)
class FrequencyThresholdConfig:
    """Параметры алгоритма. Дефолты — разумные для арбитража узких GEO."""

    bucket_step: Decimal = Decimal("0.5")
    baseline_max_frequency: Decimal = Decimal("2.0")
    degradation_pct: Decimal = Decimal("30")
    min_samples_per_bucket: int = 5
    min_baseline_samples: int = 10
    min_total_samples: int = 30
    min_threshold: Decimal = Decimal("1.5")
    max_threshold: Decimal = Decimal("10.0")


@dataclass(frozen=True)
class FrequencyThresholdResult:
    """Результат расчёта. threshold=None — порог не определён (см. reason)."""

    threshold: Decimal | None
    baseline_metric: Decimal | None
    total_samples: int
    reason: str


def _bucket_floor(freq: Decimal, step: Decimal) -> Decimal:
    """Нижняя граница бакета частоты (floor к кратному step)."""
    n = (freq / step).to_integral_value(rounding=ROUND_FLOOR)
    return (n * step).quantize(_STEP, rounding=ROUND_HALF_UP)


def compute_frequency_threshold(
    points: list[tuple[Decimal, Decimal]],
    config: FrequencyThresholdConfig | None = None,
) -> FrequencyThresholdResult:
    """Считает порог частоты по точкам (frequency, metric_value).

    metric_value — «чем выше, тем хуже» (cost_per_result и т.п.). Pure-функция:
    одинаковый вход → одинаковый выход, без I/O.
    """
    cfg = config or FrequencyThresholdConfig()

    # 1. Валидные точки: положительная частота, неотрицательная метрика.
    valid = [
        (Decimal(f), Decimal(m))
        for f, m in points
        if f is not None and m is not None and Decimal(f) > 0 and Decimal(m) >= 0
    ]
    total = len(valid)
    if total < cfg.min_total_samples:
        return FrequencyThresholdResult(
            threshold=None,
            baseline_metric=None,
            total_samples=total,
            reason=f"недостаточно данных: {total} < {cfg.min_total_samples}",
        )

    # 2. baseline — медиана метрики на низкой частоте.
    baseline_vals = [m for f, m in valid if f < cfg.baseline_max_frequency]
    if len(baseline_vals) < cfg.min_baseline_samples:
        return FrequencyThresholdResult(
            threshold=None,
            baseline_metric=None,
            total_samples=total,
            reason=(
                f"нет baseline: точек на частоте <{cfg.baseline_max_frequency} "
                f"всего {len(baseline_vals)} < {cfg.min_baseline_samples}"
            ),
        )
    baseline = Decimal(statistics.median(baseline_vals)).quantize(_STEP, rounding=ROUND_HALF_UP)

    # baseline == 0 (например cost_per_result=0 на старте) — деградацию не от чего считать.
    if baseline <= 0:
        return FrequencyThresholdResult(
            threshold=None,
            baseline_metric=baseline,
            total_samples=total,
            reason="baseline метрика = 0, деградацию не вычислить",
        )

    degraded_level = baseline * (Decimal("1") + cfg.degradation_pct / Decimal("100"))

    # 3. Группируем по бакетам, считаем медиану на бакет.
    buckets: dict[Decimal, list[Decimal]] = defaultdict(list)
    for f, m in valid:
        buckets[_bucket_floor(f, cfg.bucket_step)].append(m)

    # 4. Идём по бакетам вверх от baseline_max_frequency.
    for bucket_low in sorted(buckets):
        if bucket_low < cfg.baseline_max_frequency:
            continue
        vals = buckets[bucket_low]
        if len(vals) < cfg.min_samples_per_bucket:
            continue
        median_metric = Decimal(statistics.median(vals))
        if median_metric >= degraded_level:
            threshold = max(cfg.min_threshold, min(bucket_low, cfg.max_threshold))
            return FrequencyThresholdResult(
                threshold=threshold.quantize(_STEP, rounding=ROUND_HALF_UP),
                baseline_metric=baseline,
                total_samples=total,
                reason=(
                    f"деградация на частоте ≥{bucket_low}: метрика "
                    f"{median_metric.quantize(_STEP)} ≥ порога деградации "
                    f"{degraded_level.quantize(_STEP)} (baseline {baseline})"
                ),
            )

    return FrequencyThresholdResult(
        threshold=None,
        baseline_metric=baseline,
        total_samples=total,
        reason="деградации частоты не обнаружено — порог не выставляем",
    )


async def analyze_offer_frequency(
    engine: AsyncEngine,
    *,
    offer_id: str,
    days: int = 14,
    config: FrequencyThresholdConfig | None = None,
) -> FrequencyThresholdResult:
    """Загружает (frequency, cost_per_result) из ad_metrics по офферу и считает порог.

    Read-only. Партиционная таблица ad_metrics фильтруется по cycle_ts (партиционный
    ключ) — обязательно для partition pruning.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT m.frequency, m.cost_per_result
                    FROM ad_metrics m
                    JOIN fb_ads a ON a.id = m.ad_id
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE c.offer_id = :oid
                      AND m.cycle_ts >= NOW() - make_interval(days => :days)
                      AND m.frequency IS NOT NULL
                      AND m.cost_per_result IS NOT NULL
                    """
                ),
                {"oid": offer_id, "days": int(days)},
            )
        ).all()

    points = [(r[0], r[1]) for r in rows]
    return compute_frequency_threshold(points, config)


async def apply_recommended_threshold(
    engine: AsyncEngine,
    *,
    offer_id: str,
    days: int = 14,
    dry_run: bool = True,
    config: FrequencyThresholdConfig | None = None,
) -> tuple[FrequencyThresholdResult, bool]:
    """Считает порог по офферу и (если не dry_run) пишет его в offer_rules.

    Возвращает (result, applied). applied=True только если запись реально произошла.

    Защита money: пишем ТОЛЬКО когда threshold определён, dry_run=False И текущий
    offer_rules.frequency_threshold IS NULL (не затираем ручные/ранее выставленные
    значения — порог управляет авто-стопом, перезапись без спроса недопустима).
    """
    result = await analyze_offer_frequency(engine, offer_id=offer_id, days=days, config=config)
    if result.threshold is None or dry_run:
        return result, False

    async with engine.begin() as conn:
        upd = await conn.execute(
            text(
                """
                UPDATE offer_rules
                SET frequency_threshold = :thr, updated_at = NOW()
                WHERE offer_id = :oid AND frequency_threshold IS NULL
                """
            ),
            {"thr": result.threshold, "oid": offer_id},
        )
    applied = bool(upd.rowcount and upd.rowcount > 0)
    if applied:
        logger.info(
            "frequency_analyzer: оффер %s → frequency_threshold=%s (%s)",
            offer_id,
            result.threshold,
            result.reason,
        )
    return result, applied


__all__ = [
    "FrequencyThresholdConfig",
    "FrequencyThresholdResult",
    "analyze_offer_frequency",
    "apply_recommended_threshold",
    "compute_frequency_threshold",
]
