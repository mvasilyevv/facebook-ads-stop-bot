# -*- coding: utf-8 -*-
"""Сторожок «тени отчётности Meta» — pure-детектор рассинхрона биллинга и пер-адной отчётности.

ПРОБЛЕМА (замер на проде 03.07 08:31–09:40 UTC): биллинговый счётчик кабинета
``amount_spent`` (GET act_{id}?fields=amount_spent, lifetime в minor units) двигается
РАНЬШЕ пер-адной отчётности am_tabular — первый тик 08:38 против 08:41, за час
биллинг и отчётность расходятся. Биллинг видит «тень» открута, которую снимки ещё
не показали. Money-класс: утренние перекруты (18 минут нулей при реальном откруте).

СМЫСЛ: «кабинет списывает деньги, а пер-адная отчётность стоит» → CRITICAL владельцу.
Alert-only, без авто-паузы. Детектор чистый (pure) — на вход последовательность
снимков, на выход вердикт либо None.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

# ====================== дефолтные пороги ======================
# Окно среза — сколько секунд назад берём «старейший» снимок для сравнения.
DEFAULT_WINDOW_SECONDS = 360
# Биллинг должен вырасти минимум на столько minor units, чтобы считать «кабинет тратит».
DEFAULT_BILLING_MIN_DELTA_MINOR = 25
# Пер-адная отчётность должна стоять (Δ ≤ этого): иначе скан «видит» открут — не тень.
DEFAULT_REPORTED_MAX_DELTA_MINOR = 5


@dataclass(frozen=True)
class ShadowSample:
    """Один снимок: биллинг кабинета vs пер-адная отчётность на момент ts.

    Both counters use the confirmed currency's integer minor unit.  The
    currency is part of every sample so evidence from different monetary
    units can never be compared.
    """

    ts: datetime
    currency: str
    billing_minor: int
    reported_minor: int


@dataclass(frozen=True)
class ShadowVerdict:
    """Вердикт: кабинет тратит, а отчётность стоит. Несёт дельты/окно для текста алерта."""

    currency: str
    billing_delta_minor: int  # прирост биллинга за окно
    reported_delta_minor: int  # прирост пер-адной отчётности за окно (≈0)
    window_seconds: int  # фактический интервал между старейшим-в-окне и новейшим снимком
    oldest_ts: datetime  # ts старейшего снимка в окне
    newest_ts: datetime  # ts новейшего снимка


def detect_shadow(
    samples: Sequence[ShadowSample],
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    billing_min_delta_minor: int = DEFAULT_BILLING_MIN_DELTA_MINOR,
    reported_max_delta_minor: int = DEFAULT_REPORTED_MAX_DELTA_MINOR,
) -> ShadowVerdict | None:
    """Ищет «тень отчётности»: биллинг вырос, пер-адная отчётность стоит.

    Берёт срез за последние ``window_seconds`` относительно новейшего снимка:
    старейший снимок В ОКНЕ vs новейший. Тревога, когда:
      Δbilling ≥ billing_min_delta_minor  И  Δreported ≤ reported_max_delta_minor.

    None, если данных мало (< 2 снимков), окно вырождается (все снимки — один момент)
    либо условие не выполнено. Δ считаются по неубыванию: отрицательные приросты
    (сброс биллинга/отчётности на границе суток) не дают тревогу — Δbilling < порога.
    """
    if window_seconds <= 0:
        raise ValueError("shadow window_seconds must be positive")
    if billing_min_delta_minor <= 0:
        raise ValueError("shadow billing threshold must be positive")
    if reported_max_delta_minor < 0:
        raise ValueError("shadow reporting tolerance must be non-negative")
    if len(samples) < 2:
        return None
    if any(sample.billing_minor < 0 or sample.reported_minor < 0 for sample in samples):
        raise ValueError("shadow counters must be non-negative minor units")
    currencies = {sample.currency for sample in samples}
    if len(currencies) != 1:
        raise ValueError("shadow samples must use exactly one confirmed currency")
    currency = next(iter(currencies))

    # Хронологический порядок: не полагаемся на порядок входа (Redis-лист — LIFO).
    ordered = sorted(samples, key=lambda s: s.ts)
    newest = ordered[-1]

    # Старейший снимок, попадающий в окно [newest.ts - window, newest.ts].
    # Более старые (вне окна) отсекаются — тревога только по свежему срезу.
    oldest_in_window: ShadowSample | None = None
    for sample in ordered:
        age = (newest.ts - sample.ts).total_seconds()
        if age <= window_seconds:
            oldest_in_window = sample
            break

    if oldest_in_window is None or oldest_in_window.ts == newest.ts:
        # В окне только новейший снимок — сравнивать не с чем.
        return None

    billing_delta = newest.billing_minor - oldest_in_window.billing_minor
    reported_delta = newest.reported_minor - oldest_in_window.reported_minor

    if billing_delta < billing_min_delta_minor:
        return None
    if reported_delta > reported_max_delta_minor:
        return None

    actual_window = int((newest.ts - oldest_in_window.ts).total_seconds())
    return ShadowVerdict(
        currency=currency,
        billing_delta_minor=billing_delta,
        reported_delta_minor=reported_delta,
        window_seconds=actual_window,
        oldest_ts=oldest_in_window.ts,
        newest_ts=newest.ts,
    )
