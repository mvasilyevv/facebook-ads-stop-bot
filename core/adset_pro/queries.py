# -*- coding: utf-8 -*-
"""Read-only helpers поверх adsetpro_postback_events для observer/pipeline.

Используются в evaluator-стороне (через RuleContext.external_deposits) — закрывают
gap attribution, когда Meta Ads Manager не видит депозит, а трекер AdSet.pro его
уже зарегистрировал. См. META_INTEGRATION_PLAN.md §4.4 / Этап 6.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Compatibility constant. Money logic no longer counts raw event types: only the
# monotonic click projection with both registration and FTD protects from STOP.
DEPOSIT_EVENT_TYPES: tuple[str, ...] = ("confirmed_deposit",)

# Окно для подсчёта внешних депозитов — должно быть достаточно для closure attribution
# (FB атрибутирует кликами 24h по умолчанию, депозиты тоже обычно в этом окне).
#
# MID-3 (аудит 02.07): окно 24ч широковато для no-dep guardrail'ов в evaluator'е —
# ОДИН депозит, попавший в это окно, "защищает" объявление от всех no-dep стоп-правил
# на ВСЕ оставшиеся часы окна, даже если объявление после этого депозита реально льёт
# в минус весь остаток суток. Это осознанный трейд-офф в пользу меньшего false-positive
# (не остановить объявление, которое на самом деле конвертит с лагом), а не баг — но при
# желании ужесточить guardrail'ы окно можно сузить параметром `window=` у
# load_external_deposits/load_external_deposits_batch (например, до 2-6ч) без изменения
# сигнатуры или контракта функций.
DEFAULT_EXTERNAL_DEPOSITS_WINDOW = timedelta(hours=24)


def _query_window(
    *,
    window: timedelta,
    now: datetime | None,
    window_start: datetime | None,
    window_end: datetime | None,
) -> tuple[datetime, datetime]:
    """Resolve an explicit half-open interval, retaining the legacy rolling default."""
    end = window_end or now or datetime.now(timezone.utc)
    start = window_start or (end - window)
    if start > end:
        raise ValueError("tracker query window_start must not be later than window_end")
    return start, end


async def load_external_deposits(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
    window: timedelta = DEFAULT_EXTERNAL_DEPOSITS_WINDOW,
    now: datetime | None = None,
    event_types: tuple[str, ...] = DEPOSIT_EVENT_TYPES,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> int:
    """Сколько депозитных событий прислал AdSet.pro для одного fb_ad_id за window.

    Дубликаты (is_duplicate=TRUE) не учитываем — они уже есть в основной записи.

    MID-3: window по умолчанию — 24ч (см. DEFAULT_EXTERNAL_DEPOSITS_WINDOW) — один
    депозит внутри окна глушит no-dep guardrail'ы evaluator'а на всё окно; сузить
    можно передав явный `window=`.
    """
    del event_types
    cutoff, until = _query_window(
        window=window,
        now=now,
        window_start=window_start,
        window_end=window_end,
    )
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT COUNT(*) FROM tracker_click_state
                WHERE fb_ad_id = :fb_ad_id
                  AND registration = TRUE
                  AND ftd = TRUE
                  AND confirmed_deposit = TRUE
                  AND confirmed_deposit_at >= :since
                  AND confirmed_deposit_at < :until
                """
            ),
            {
                "fb_ad_id": fb_ad_id,
                "since": cutoff,
                "until": until,
            },
        )
        return int(result.scalar() or 0)


async def load_external_deposits_batch(
    engine: AsyncEngine,
    *,
    fb_ad_ids: list[str],
    window: timedelta = DEFAULT_EXTERNAL_DEPOSITS_WINDOW,
    now: datetime | None = None,
    event_types: tuple[str, ...] = DEPOSIT_EVENT_TYPES,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, int]:
    """Batch-версия: возвращает {fb_ad_id: count} одним SQL для всего scan-цикла.

    Если для какого-то fb_ad_id нет постбэков — в результате этого ключа не будет
    (потребитель должен использовать .get(fb_ad_id, 0)).

    MID-3: window по умолчанию — 24ч (см. DEFAULT_EXTERNAL_DEPOSITS_WINDOW) — один
    депозит внутри окна глушит no-dep guardrail'ы evaluator'а на всё окно; сузить
    можно передав явный `window=`.
    """
    if not fb_ad_ids:
        return {}
    del event_types
    cutoff, until = _query_window(
        window=window,
        now=now,
        window_start=window_start,
        window_end=window_end,
    )
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT fb_ad_id, COUNT(*)
                FROM tracker_click_state
                WHERE fb_ad_id = ANY(:fb_ad_ids)
                  AND registration = TRUE
                  AND ftd = TRUE
                  AND confirmed_deposit = TRUE
                  AND confirmed_deposit_at >= :since
                  AND confirmed_deposit_at < :until
                GROUP BY fb_ad_id
                """
            ),
            {
                "fb_ad_ids": fb_ad_ids,
                "since": cutoff,
                "until": until,
            },
        )
        return {row[0]: int(row[1]) for row in result.all()}


async def load_external_registrations_batch(
    engine: AsyncEngine,
    *,
    fb_ad_ids: list[str],
    window: timedelta = DEFAULT_EXTERNAL_DEPOSITS_WINDOW,
    now: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, int]:
    """Live registrations by ad without adding them to delayed Meta counts."""
    if not fb_ad_ids:
        return {}
    cutoff, until = _query_window(
        window=window,
        now=now,
        window_start=window_start,
        window_end=window_end,
    )
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT fb_ad_id, COUNT(*)
                    FROM tracker_click_state
                    WHERE fb_ad_id = ANY(:fb_ad_ids)
                      AND registration = TRUE
                      AND registration_at >= :since
                      AND registration_at < :until
                    GROUP BY fb_ad_id
                    """
                ),
                {"fb_ad_ids": fb_ad_ids, "since": cutoff, "until": until},
            )
        ).all()
    return {row[0]: int(row[1]) for row in rows}
