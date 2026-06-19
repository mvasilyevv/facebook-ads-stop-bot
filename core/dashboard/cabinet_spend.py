# -*- coding: utf-8 -*-
"""Спенд ТЕКУЩИХ суток кабинета с нуля (Волна 2 / E).

ПРОБЛЕМА: `ad_metrics` кумулятивны и обнуляются Meta в полночь ПО ТАЙМЗОНЕ
аккаунта. Дашборд показывал «текущий спенд» как сумму серии за скользящие 24ч →
(а) суммирование кумулятивных снимков завышает (урок CRIT-1), (б) окно пересекает
полночь и подмешивает спенд прошлого дня.

РЕШЕНИЕ: «текущий спенд» = latest-per-ad snapshot, отсечённый по началу текущих
суток кабинета (`cabinet_day_start_utc`), просуммированный по объявлениям. Граница
per-account (мульти-кабинет: у каждого ad_account_id своя таймзона). Граница сама
сдвигается каждую полночь кабинета → «с нуля» работает автоматически каждые сутки.

partition pruning: явный нижний пол `prune_floor` (константа) сохраняет prune по
`cycle_ts` (partition key), даже когда per-row граница — выражение COALESCE.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


def cabinet_day_start_utc(offset_hours: float, now: datetime) -> datetime:
    """Начало текущих суток кабинета в UTC.

    Аккаунт-локальное время = now + offset; обнуляем до полуночи; возвращаем в UTC (−offset).
    Поддерживает дробные оффсеты (напр. +5.5 для India). now должен быть tz-aware UTC.
    """
    local = now + timedelta(hours=offset_hours)
    local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight - timedelta(hours=offset_hours)


async def current_day_spend(
    engine: AsyncEngine,
    *,
    tz_map: dict[str, float],
    default_offset: float,
    now: datetime,
) -> Decimal:
    """Суммарный spend текущих суток кабинета (latest-per-ad с полом cabinet_day_start).

    tz_map: ad_account_id → offset_hours (per-account границы). default_offset — для
    объявлений без известного кабинета (NULL/не в карте). Считается latest-per-ad
    (LIMIT 1, НЕ SUM серии) с порогом `cycle_ts >= cabinet_day_start[account]`, затем
    SUM по объявлениям. Ад без снимка после полуночи → latest NULL → не учтён (его
    спенд обнулился, новый ещё не сканировался).
    """
    default_boundary = cabinet_day_start_utc(default_offset, now)
    boundaries = {aid: cabinet_day_start_utc(off, now) for aid, off in tz_map.items()}
    # Нижний пол для partition pruning: самая ранняя граница − сутки (запас).
    prune_floor = min([default_boundary, *boundaries.values()]) - timedelta(days=1)

    params: dict[str, Any] = {
        "default_boundary": default_boundary,
        "prune_floor": prune_floor,
    }

    # Per-account граница как CASE по ad_account_id (CAST(:p AS timestamptz), НЕ :p::ts —
    # `::cast` рядом с bind-параметром ломает парсер text() в asyncpg). Без VALUES/CTE.
    when_clauses: list[str] = []
    for i, (aid, bnd) in enumerate(boundaries.items()):
        params[f"acct{i}"] = aid
        params[f"bnd{i}"] = bnd
        when_clauses.append(f"WHEN :acct{i} THEN CAST(:bnd{i} AS timestamptz)")
    if when_clauses:
        boundary_expr = (
            "CASE fc.ad_account_id "
            + " ".join(when_clauses)
            + " ELSE CAST(:default_boundary AS timestamptz) END"
        )
    else:
        boundary_expr = "CAST(:default_boundary AS timestamptz)"

    sql = f"""
    SELECT COALESCE(SUM(latest.spend), 0) AS total
    FROM fb_ads fa
    JOIN fb_adsets fas ON fas.id = fa.adset_id
    JOIN fb_campaigns fc ON fc.id = fas.campaign_id
    LEFT JOIN LATERAL (
        SELECT m.spend
        FROM ad_metrics m
        WHERE m.ad_id = fa.id
          AND m.cycle_ts >= :prune_floor
          AND m.cycle_ts >= ({boundary_expr})
        ORDER BY m.cycle_ts DESC
        LIMIT 1
    ) latest ON true
    WHERE fa.is_active = true
    """
    async with engine.connect() as conn:
        row = (await conn.execute(text(sql), params)).one()
    return Decimal(str(row.total)) if row.total is not None else Decimal("0")
