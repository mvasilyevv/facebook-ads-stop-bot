# -*- coding: utf-8 -*-
"""Адаптивный интервал сканирования: частота скана зависит от близости к стопу.

Модель «гибрид»: базовый интервал (`observer_config.interval_seconds`, слайдер в UI)
трактуется как CALM-режим (спокойное состояние). Остальные режимы — множители от него:
у порога сканируем чаще (ловим стоп раньше → меньше перерасход), на холостом ходу — реже.

Уровень угрозы берётся из итога scan-цикла (`CycleResult`): фактические stop/warning-хиты
FSM. WARNING в v2 = 80% от стоп-порога, т.е. сам по себе сигнал приближения к границе.

Все функции чистые (без I/O) — тестируются изолированно.
"""

from __future__ import annotations

# Множители интервала относительно базового (CALM) значения.
# CRITICAL — есть stop-хиты: объявление за порогом, нужен максимально частый скан.
# ELEVATED — есть warning-хиты: приближение к порогу.
# CALM     — есть объявления с офферами, но без алертов: штатный темп (=база).
# IDLE     — нет объявлений с офферами: сканировать незачем часто.
SCAN_MODE_MULTIPLIERS: dict[str, float] = {
    "CRITICAL": 0.2,
    "ELEVATED": 0.5,
    "CALM": 1.0,
    "IDLE": 1.5,
}

# Жёсткие границы итогового интервала (защита от перекрута сканера и от вечного сна).
# MIN: сканер физически не успевает чаще + anti-detect; ниже опускаться нельзя даже после jitter.
# MAX: верх UI-слайдера (observer_config.interval_seconds).
MIN_INTERVAL_SECONDS: float = 10.0
MAX_INTERVAL_SECONDS: float = 600.0

# Доля случайного разброса (±) от рассчитанного интервала — против строгой регулярности.
JITTER_FRACTION: float = 0.10


def select_scan_mode(
    *,
    alerts_stop: int,
    alerts_warning: int,
    rows_with_offer: int,
) -> str:
    """Выбирает режим скана по итогу цикла. Приоритет: stop > warning > офферные ads > пусто.

    Args:
        alerts_stop: число stop-переходов FSM в этом цикле.
        alerts_warning: число warning-переходов FSM в этом цикле.
        rows_with_offer: число просканированных объявлений, сматченных с оффером.

    Returns:
        Имя режима: "CRITICAL" | "ELEVATED" | "CALM" | "IDLE".
    """
    if alerts_stop > 0:
        return "CRITICAL"
    if alerts_warning > 0:
        return "ELEVATED"
    if rows_with_offer > 0:
        return "CALM"
    return "IDLE"


def resolve_scan_mode(summary: dict) -> str:
    """Выбирает режим по summary одного цикла, с учётом нештатных исходов.

    Нештатные outcome не несут данных об угрозе (cycle_result=None), поэтому
    решаются явно:
        paused → IDLE  — скан выключен пользователем, спим реже (trigger будит мгновенно);
        error  → CALM  — скан упал, ретраим в штатном темпе, НЕ замедляемся до IDLE
                          (иначе горящее объявление ждёт дольше при сбое сканера).
    Штатные success/empty — по фактической угрозе через select_scan_mode
    (empty даёт rows_with_offer=0 → IDLE, что корректно: сканировать нечего).
    """
    outcome = summary.get("outcome")
    if outcome == "paused":
        return "IDLE"
    if outcome == "error":
        return "CALM"
    return select_scan_mode(
        alerts_stop=summary.get("alerts_stop", 0),
        alerts_warning=summary.get("alerts_warning", 0),
        rows_with_offer=summary.get("rows_with_offer", 0),
    )


def clamp_interval(seconds: float) -> float:
    """Зажимает интервал в [MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS]."""
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, seconds))


def compute_adaptive_interval(base_interval_seconds: float, mode: str) -> float:
    """Считает интервал до следующего скана: база × множитель режима, с clamp.

    Args:
        base_interval_seconds: базовый интервал (CALM) из observer_config.interval_seconds.
        mode: режим из select_scan_mode. Неизвестный режим → множитель 1.0 (как CALM).

    Returns:
        Интервал в секундах, зажатый в [MIN, MAX]. Без jitter (jitter применяется отдельно).
    """
    multiplier = SCAN_MODE_MULTIPLIERS.get(mode, 1.0)
    return clamp_interval(base_interval_seconds * multiplier)
