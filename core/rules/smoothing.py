# -*- coding: utf-8 -*-
"""Bayesian-сглаживание метрик при малых выборках.

При lead/registration < prior_weight значение метрики «тянется» к медиане
оффера, чтобы не ловить статистический шум на старте объявления.
"""

from __future__ import annotations

from decimal import Decimal


def smoothed_metric(
    current_value: Decimal,
    sample_size: int,
    offer_median: Decimal | None,
    prior_weight: int = 5,
) -> Decimal:
    """Возвращает Bayesian-сглаженное значение метрики.

    Формула: (prior_weight * median + sample_size * current) / (prior_weight + sample_size)

    Сглаживание применяется только при:
    - offer_median не None (для оффера есть история)
    - sample_size < prior_weight (выборка ещё мала)

    При достижении prior_weight событий возвращается сырое current_value.
    """
    if offer_median is None:
        # Нет истории по офферу — не сглаживаем, старое поведение
        return current_value
    if sample_size >= prior_weight:
        # Выборки достаточно — сглаживание не нужно
        return current_value
    total = prior_weight + sample_size
    return (Decimal(prior_weight) * offer_median + Decimal(sample_size) * current_value) / Decimal(
        total
    )
