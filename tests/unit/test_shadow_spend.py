# -*- coding: utf-8 -*-
"""Unit: core/meta_api/shadow_spend.py — pure-детектор «тени отчётности Meta».

Сторожок ловит money-класс перекрута: биллинг кабинета (amount_spent) растёт, а
пер-адная отчётность (am_tabular) стоит → реальный открут не виден скану. Здесь
проверяем pure detect_shadow на ТОЧНЫХ значениях центов (money — не shape).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.meta_api.shadow_spend import (
    DEFAULT_BILLING_MIN_DELTA_MINOR,
    ShadowSample,
    detect_shadow,
)

_T0 = datetime(2026, 7, 3, 8, 30, 0, tzinfo=timezone.utc)


def _sample(offset_seconds: int, billing: int, reported: int) -> ShadowSample:
    """Снимок со сдвигом offset_seconds от _T0 (для читаемости хронологии в тестах)."""
    return ShadowSample(
        ts=_T0 + timedelta(seconds=offset_seconds),
        currency="USD",
        billing_minor=billing,
        reported_minor=reported,
    )


# Тень отчётности: биллинг +30¢, отчётность +0¢ → тревога с точными дельтами и окном
def test_detect_shadow_billing_moves_reported_stands() -> None:
    samples = [
        _sample(0, 1000, 500),
        _sample(300, 1030, 500),  # +30¢ билл, +0¢ отчётность за 300с
    ]
    verdict = detect_shadow(samples, window_seconds=360)
    assert verdict is not None
    assert verdict.currency == "USD"
    assert verdict.billing_delta_minor == 30
    assert verdict.reported_delta_minor == 0
    assert verdict.window_seconds == 300


# Оба растут синхронно (билл +30¢, отчётность +28¢) → скан видит открут, тени нет → None
def test_detect_shadow_both_move_no_alert() -> None:
    samples = [
        _sample(0, 1000, 500),
        _sample(300, 1030, 528),  # отчётность +28¢ > допуска 5¢
    ]
    assert detect_shadow(samples, window_seconds=360) is None


# Граница: билл +24¢ (< порога 25¢), отчётность стоит → None (не дотянули до тревоги)
def test_detect_shadow_billing_below_threshold_boundary() -> None:
    samples = [
        _sample(0, 1000, 500),
        _sample(300, 1024, 500),  # +24¢ < 25¢
    ]
    assert detect_shadow(samples, window_seconds=360) is None
    assert DEFAULT_BILLING_MIN_DELTA_MINOR == 25  # фиксируем дефолт-контракт


# Ровно на пороге: билл +25¢, отчётность стоит → тревога (≥ порога, не >)
def test_detect_shadow_billing_exactly_at_threshold() -> None:
    samples = [
        _sample(0, 1000, 500),
        _sample(300, 1025, 500),  # ровно +25¢
    ]
    verdict = detect_shadow(samples, window_seconds=360, billing_min_delta_minor=25)
    assert verdict is not None
    assert verdict.billing_delta_minor == 25


# Отчётность ровно на допуске +5¢ → тревога (Δreported ≤ 5, включительно)
def test_detect_shadow_reported_exactly_at_tolerance() -> None:
    samples = [
        _sample(0, 1000, 500),
        _sample(300, 1030, 505),  # +5¢ отчётность = допуск
    ]
    verdict = detect_shadow(samples, window_seconds=360, reported_max_delta_minor=5)
    assert verdict is not None
    assert verdict.reported_delta_minor == 5


# Окно отсекает старые сэмплы: старейший (билл 1000) вне окна 360с, срез внутри окна
# показывает билл +5¢ < порога → None (сравниваем НЕ с самым первым, а со старейшим В ОКНЕ)
def test_detect_shadow_window_drops_old_samples() -> None:
    samples = [
        _sample(0, 1000, 500),  # вне окна (age от newest = 700с > 360с)
        _sample(400, 1050, 500),  # старейший В ОКНЕ (age = 300с)
        _sample(700, 1055, 500),  # newest: билл +5¢ относительно 1050 → мало
    ]
    assert detect_shadow(samples, window_seconds=360) is None


# Тот же набор, но внутри окна биллинг реально прыгнул +30¢ → тревога по срезу окна
def test_detect_shadow_window_uses_oldest_in_window() -> None:
    samples = [
        _sample(0, 5000, 500),  # вне окна — игнор (иначе Δ был бы огромным)
        _sample(400, 1000, 500),  # старейший в окне
        _sample(700, 1030, 500),  # newest: +30¢ за срез окна
    ]
    verdict = detect_shadow(samples, window_seconds=360)
    assert verdict is not None
    assert verdict.billing_delta_minor == 30
    assert verdict.window_seconds == 300


# Пустой список → None (нечего сравнивать)
def test_detect_shadow_empty() -> None:
    assert detect_shadow([], window_seconds=360) is None


# Один сэмпл → None (нужна пара для дельты)
def test_detect_shadow_single_sample() -> None:
    assert detect_shadow([_sample(0, 1000, 500)], window_seconds=360) is None


# Все снимки одномоментны (окно вырождается) → None, деления/сравнения не падают
def test_detect_shadow_degenerate_window_same_ts() -> None:
    samples = [
        ShadowSample(
            ts=_T0,
            currency="USD",
            billing_minor=1000,
            reported_minor=500,
        ),
        ShadowSample(
            ts=_T0,
            currency="USD",
            billing_minor=2000,
            reported_minor=500,
        ),
    ]
    assert detect_shadow(samples, window_seconds=360) is None


# Порядок входа неважен: Redis-лист LIFO (новейший в голове) → детектор сам сортирует
def test_detect_shadow_unordered_input() -> None:
    # newest сначала (как отдаёт lrange по lpush-списку)
    samples = [
        _sample(300, 1030, 500),
        _sample(0, 1000, 500),
    ]
    verdict = detect_shadow(samples, window_seconds=360)
    assert verdict is not None
    assert verdict.billing_delta_minor == 30


# Сброс биллинга на границе суток (Δ отрицательна) не даёт ложную тревогу
def test_detect_shadow_billing_reset_negative_delta() -> None:
    samples = [
        _sample(0, 5000, 4000),
        _sample(300, 20, 0),  # полночь кабинета: оба обнулились
    ]
    assert detect_shadow(samples, window_seconds=360) is None


def test_detect_shadow_rejects_cross_currency_evidence() -> None:
    samples = [
        _sample(0, 1000, 500),
        ShadowSample(
            ts=_T0 + timedelta(seconds=300),
            currency="KES",
            billing_minor=1030,
            reported_minor=500,
        ),
    ]

    with pytest.raises(ValueError, match="one confirmed currency"):
        detect_shadow(samples, window_seconds=360)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"window_seconds": 0}, "window_seconds"),
        ({"billing_min_delta_minor": 0}, "billing threshold"),
        ({"reported_max_delta_minor": -1}, "reporting tolerance"),
    ],
)
def test_detect_shadow_rejects_unsafe_detector_configuration(
    kwargs: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        detect_shadow([_sample(0, 1000, 500), _sample(300, 1030, 500)], **kwargs)


def test_detect_shadow_rejects_negative_counters() -> None:
    with pytest.raises(ValueError, match="non-negative minor units"):
        detect_shadow(
            [
                _sample(0, -1, 0),
                _sample(300, 30, 0),
            ],
            window_seconds=360,
        )
