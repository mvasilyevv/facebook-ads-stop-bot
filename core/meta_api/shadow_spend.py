# -*- coding: utf-8 -*-
"""Сторожок «тени отчётности Meta» — pure-детектор рассинхрона биллинга и пер-адной отчётности.

ПРОБЛЕМА (замер на проде 03.07 08:31–09:40 UTC): биллинговый счётчик кабинета
``amount_spent`` (GET act_{id}?fields=amount_spent, lifetime в центах) двигается
РАНЬШЕ пер-адной отчётности am_tabular — первый тик 08:38 против 08:41, за час
+$1.54 против +$1.25. Биллинг видит «тень» открута, которую пер-адные снимки ещё
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
# Биллинг должен вырасти минимум на столько центов, чтобы считать «кабинет тратит».
DEFAULT_BILLING_MIN_DELTA_CENTS = 25
# Пер-адная отчётность должна стоять (Δ ≤ этого): иначе скан «видит» открут — не тень.
DEFAULT_REPORTED_MAX_DELTA_CENTS = 5


@dataclass(frozen=True)
class ShadowSample:
    """Один снимок: биллинг кабинета vs пер-адная отчётность на момент ts.

    billing_cents — lifetime ``amount_spent`` кабинета в центах (GET act_{id}).
    reported_cents — суммарный спенд текущих суток по пер-адным снимкам ×100 (int).
    """

    ts: datetime
    billing_cents: int
    reported_cents: int


@dataclass(frozen=True)
class ShadowVerdict:
    """Вердикт: кабинет тратит, а отчётность стоит. Несёт дельты/окно для текста алерта."""

    billing_delta_cents: int  # прирост биллинга за окно
    reported_delta_cents: int  # прирост пер-адной отчётности за окно (≈0)
    window_seconds: int  # фактический интервал между старейшим-в-окне и новейшим снимком
    oldest_ts: datetime  # ts старейшего снимка в окне
    newest_ts: datetime  # ts новейшего снимка


def detect_shadow(
    samples: Sequence[ShadowSample],
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    billing_min_delta_cents: int = DEFAULT_BILLING_MIN_DELTA_CENTS,
    reported_max_delta_cents: int = DEFAULT_REPORTED_MAX_DELTA_CENTS,
) -> ShadowVerdict | None:
    """Ищет «тень отчётности»: биллинг вырос, пер-адная отчётность стоит.

    Берёт срез за последние ``window_seconds`` относительно новейшего снимка:
    старейший снимок В ОКНЕ vs новейший. Тревога, когда:
      Δbilling ≥ billing_min_delta_cents  И  Δreported ≤ reported_max_delta_cents.

    None, если данных мало (< 2 снимков), окно вырождается (все снимки — один момент)
    либо условие не выполнено. Δ считаются по неубыванию: отрицательные приросты
    (сброс биллинга/отчётности на границе суток) не дают тревогу — Δbilling < порога.
    """
    if len(samples) < 2:
        return None

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

    billing_delta = newest.billing_cents - oldest_in_window.billing_cents
    reported_delta = newest.reported_cents - oldest_in_window.reported_cents

    if billing_delta < billing_min_delta_cents:
        return None
    if reported_delta > reported_max_delta_cents:
        return None

    actual_window = int((newest.ts - oldest_in_window.ts).total_seconds())
    return ShadowVerdict(
        billing_delta_cents=billing_delta,
        reported_delta_cents=reported_delta,
        window_seconds=actual_window,
        oldest_ts=oldest_in_window.ts,
        newest_ts=newest.ts,
    )
