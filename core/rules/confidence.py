# -*- coding: utf-8 -*-
"""Применение ML-confidence к порогам правил.

confidence вычисляется из истории AlertEvent:
  alerts_confirmed / alerts_total за скользящее окно N дней.

Диапазон 0.0–1.0:
  < 0.1  — правило фактически всегда игнорируется → skip (return None).
  < 0.3  — низкий confidence → порог ×1.3 (сложнее сработать).
  > 0.8  — высокий confidence → порог ×0.9 (срабатывать раньше).
  иначе  — нейтральный диапазон, порог не меняется.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# Константы настройки
_SKIP_THRESHOLD = Decimal("0.1")  # ниже этого — правило отключено
_LOW_THRESHOLD = Decimal("0.3")  # ниже — ослабляем порог
_HIGH_THRESHOLD = Decimal("0.8")  # выше — ужесточаем порог
_LOW_MULTIPLIER = Decimal("1.3")  # коэффициент ослабления (редкие ложные срабатывания)
_HIGH_MULTIPLIER = Decimal("0.9")  # коэффициент ужесточения (поймать раньше)
_STEP = Decimal("0.01")


def apply_confidence(base_threshold: Decimal, confidence: Decimal) -> Decimal | None:
    """Применяет confidence к базовому порогу.

    Args:
        base_threshold: исходный порог правила (уже с time_weight).
        confidence: значение 0.0–1.0 из OfferRuleStat.

    Returns:
        None — если confidence настолько низкий, что правило следует пропустить.
        Decimal — скорректированный порог.
    """
    conf = Decimal(str(confidence))

    # Правило фактически всегда игнорируется → пропускаем
    if conf < _SKIP_THRESHOLD:
        return None

    threshold = Decimal(str(base_threshold))

    # Низкий confidence → ослабляем порог (сложнее сработать = меньше ложных)
    if conf < _LOW_THRESHOLD:
        return (threshold * _LOW_MULTIPLIER).quantize(_STEP, rounding=ROUND_HALF_UP)

    # Высокий confidence → ужесточаем порог (срабатывает чуть раньше)
    if conf > _HIGH_THRESHOLD:
        return (threshold * _HIGH_MULTIPLIER).quantize(_STEP, rounding=ROUND_HALF_UP)

    return threshold


def compute_confidence(alerts_total: int, alerts_confirmed: int) -> Decimal:
    """Вычисляет confidence из сырых счётчиков.

    Если алертов меньше 10 — возвращает prior 0.5 (недостаточно данных).
    """
    _MIN_SAMPLES = 10
    if alerts_total < _MIN_SAMPLES:
        return Decimal("0.5")
    ratio = Decimal(alerts_confirmed) / Decimal(alerts_total)
    return ratio.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
