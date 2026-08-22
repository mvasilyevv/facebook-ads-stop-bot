# -*- coding: utf-8 -*-
"""Read-only helpers поверх adsetpro_postback_events для observer/pipeline.

Используются в evaluator-стороне (через RuleContext.external_deposits) — закрывают
gap attribution, когда Meta Ads Manager не видит депозит, а трекер AdSet.pro его
уже зарегистрировал.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


def _validate_window(*, window_start: datetime, window_end: datetime) -> None:
    if window_start > window_end:
        raise ValueError("tracker query window_start must not be later than window_end")


async def load_external_deposits(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
    window_start: datetime,
    window_end: datetime,
) -> int:
    """Count confirmed deposits in the explicit half-open cabinet-day interval."""
    _validate_window(window_start=window_start, window_end=window_end)
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
                "since": window_start,
                "until": window_end,
            },
        )
        return int(result.scalar() or 0)


async def load_external_deposits_batch(
    engine: AsyncEngine,
    *,
    fb_ad_ids: list[str],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, int]:
    """Batch-версия: возвращает {fb_ad_id: count} одним SQL для всего scan-цикла.

    Если для какого-то fb_ad_id нет постбэков — в результате этого ключа не будет
    (потребитель должен использовать .get(fb_ad_id, 0)).

    """
    if not fb_ad_ids:
        return {}
    _validate_window(window_start=window_start, window_end=window_end)
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
                "since": window_start,
                "until": window_end,
            },
        )
        return {row[0]: int(row[1]) for row in result.all()}


async def load_ever_had_deposit_batch(
    engine: AsyncEngine,
    *,
    fb_ad_ids: list[str],
) -> dict[str, int]:
    """Возвращает {fb_ad_id: count} подтверждённых депозитов за всё время (без окна).

    Используется для ветки ever-had-deposit: объявление, однажды принёсшее депозит,
    остаётся на ветке «с депозитом» бессрочно — независимо от границы кабинетных суток.
    Оконная load_external_deposits_batch используется только для отображения счётчика.
    """
    if not fb_ad_ids:
        return {}
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
                GROUP BY fb_ad_id
                """
            ),
            {"fb_ad_ids": fb_ad_ids},
        )
        return {row[0]: int(row[1]) for row in result.all()}


async def load_external_registrations_batch(
    engine: AsyncEngine,
    *,
    fb_ad_ids: list[str],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, int]:
    """Live registrations by ad without adding them to delayed Meta counts."""
    if not fb_ad_ids:
        return {}
    _validate_window(window_start=window_start, window_end=window_end)
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
                {"fb_ad_ids": fb_ad_ids, "since": window_start, "until": window_end},
            )
        ).all()
    return {row[0]: int(row[1]) for row in rows}
