# -*- coding: utf-8 -*-
"""Чистые функции «Статистики залива»: производные метрики и почасовые дельты.

Производные (CPC/CPL/CPR/CPA, CTR, CR-ступени воронки) считаются ЗДЕСЬ, на бэке —
единый источник правды для web и mini (фронты не дублируют формулы). Деление на
ноль → None (фронт рисует «—»).

Почасовые дельты: `ad_metrics` — КУМУЛЯТИВНЫЕ снимки за сутки кабинета
(см. core/dashboard/metric_aggregation.py, CRIT-1). SQL отдаёт последний снимок
на (час × ad), а «сколько случилось именно в этот час» считается тут:
LAG-семантика строго PER-AD до суммирования по часу — иначе появление/уход
объявлений между часами ломает дельту агрегата. Объём данных мал
(объявления × 24 часа), Python вместо SQL-LAG — осознанно: одна реализация
формулы, покрытая unit-тестами без БД.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# Метрики воронки (порядок = ступени): все кумулятивные внутри суток кабинета.
FUNNEL_METRICS: tuple[str, ...] = (
    "spend",
    "impressions",
    "clicks",
    "leads",
    "registrations",
    "deposits",
)

_CENT = Decimal("0.01")


def _dec(value: Any) -> Decimal:
    """Любое числовое/None → Decimal (None → 0)."""
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _div(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """numerator/denominator с округлением до цента. Ноль в знаменателе → None."""
    if denominator == 0:
        return None
    return (numerator / denominator).quantize(_CENT, rounding=ROUND_HALF_UP)


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """numerator/denominator × 100 (2 знака). Ноль в знаменателе → None."""
    if denominator == 0:
        return None
    return (numerator * 100 / denominator).quantize(_CENT, rounding=ROUND_HALF_UP)


def compute_derived(totals: Mapping[str, Any]) -> dict[str, Decimal | None]:
    """Производные метрики воронки из тоталов. None при нуле в знаменателе.

    cpc/cpl/cpr/cpa — цена клика/лида/реги/депозита (spend / счётчик);
    ctr_pct — clicks/impressions×100;
    cr_*_pct — конверсия между соседними ступенями воронки.
    """
    spend = _dec(totals.get("spend"))
    impressions = _dec(totals.get("impressions"))
    clicks = _dec(totals.get("clicks"))
    leads = _dec(totals.get("leads"))
    registrations = _dec(totals.get("registrations"))
    deposits = _dec(totals.get("deposits"))
    return {
        "cpc": _div(spend, clicks),
        "cpl": _div(spend, leads),
        "cpr": _div(spend, registrations),
        "cpa": _div(spend, deposits),
        "ctr_pct": _pct(clicks, impressions),
        "cr_click_lead_pct": _pct(leads, clicks),
        "cr_lead_reg_pct": _pct(registrations, leads),
        "cr_reg_dep_pct": _pct(deposits, registrations),
    }


def compute_roi_pct(revenue: Any, spend: Any) -> Decimal | None:
    """ROI% = (revenue − spend) / spend × 100.

    Кросс-источник: revenue — трекер (AdSet.pro), spend — Meta. Attribution gap
    неустраним, поэтому ROI показывается в блоке трекера с пометкой. spend == 0
    или None → None (нечего делить).
    """
    spend_d = _dec(spend)
    if spend_d == 0:
        return None
    return ((_dec(revenue) - spend_d) * 100 / spend_d).quantize(_CENT, rounding=ROUND_HALF_UP)


def hourly_deltas(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Кумулятивные снимки на (час × ad) → честные дельты «сколько в этот час».

    rows — результат latest-per-ad-per-hour CTE: у каждой строки `ad_id`,
    `bucket_ts` и метрики FUNNEL_METRICS (кумулятив на конец часа, None → 0).

    Семантика (важно для денег):
    - LAG per-ad: prev — последний виденный кумулятив ЭТОГО объявления. Новое
      объявление в часе N → prev=0 → дельта = весь его кумулятив (верно: до
      этого его не было). Пропуск часа (не сканился) → накопленное придёт в
      час возврата.
    - Отрицательная дельта (Meta пересчитала метрику вниз) → клэмп в 0 для
      отображения, но prev обновляется СЫРЫМ значением — дальнейшие дельты
      считаются от нового кумулятива, деньги не задваиваются.
    - Окно вызова — строго внутри одних суток кабинета: посуточный сброс
      кумулятива сюда не попадает (граница окна = cabinet_day_start).

    Возвращает точки по часам (ASC): {ts, spend, impressions, clicks, leads,
    registrations, deposits, active_ads}.
    """
    ordered = sorted(rows, key=lambda r: (str(r["ad_id"]), r["bucket_ts"]))

    buckets: dict[Any, dict[str, Any]] = {}
    prev_by_ad: dict[Any, dict[str, Decimal]] = {}
    for row in ordered:
        ad_id = row["ad_id"]
        ts = row["bucket_ts"]
        prev = prev_by_ad.setdefault(ad_id, dict.fromkeys(FUNNEL_METRICS, Decimal("0")))
        bucket = buckets.setdefault(
            ts,
            {**dict.fromkeys(FUNNEL_METRICS, Decimal("0")), "ads": set()},
        )
        for metric in FUNNEL_METRICS:
            current = _dec(row.get(metric))
            delta = current - prev[metric]
            if delta > 0:
                bucket[metric] += delta
            prev[metric] = current  # сырое значение, не клэмп
        bucket["ads"].add(ad_id)

    points: list[dict[str, Any]] = []
    for ts in sorted(buckets):
        bucket = buckets[ts]
        point: dict[str, Any] = {"ts": ts, "active_ads": len(bucket["ads"])}
        for metric in FUNNEL_METRICS:
            value = bucket[metric]
            # spend остаётся Decimal (деньги), счётчики — int.
            point[metric] = value if metric == "spend" else int(value)
        points.append(point)
    return points


__all__ = [
    "FUNNEL_METRICS",
    "compute_derived",
    "compute_roi_pct",
    "hourly_deltas",
]
