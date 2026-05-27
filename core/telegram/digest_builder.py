# -*- coding: utf-8 -*-
"""Сбор данных для daily digest за окно 24ч (raw SQL, без ORM).

Pure-async-функции: `build_digest(engine, day_start_utc)` возвращает frozen
dataclass `DigestPayload` со всеми агрегациями.

Подводный камень с partitioned таблицами (`alert_events`, `ad_metrics`):
запросы должны явно фильтровать по партиционному ключу (`created_at`,
`cycle_ts`) — это даёт partition pruning и не сканирует исторические
партиции. Если убрать диапазон, planner вынужден читать все партиции.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class TopAdRow:
    """Одна строка топа объявлений по spend за окно."""

    ad_id: uuid.UUID
    fb_ad_id: str
    ad_name: str
    offer_code: str | None
    spend_usd: Decimal
    clicks: int
    leads: int
    cpc: Decimal | None
    cost_per_lead: Decimal | None


@dataclass(frozen=True)
class DigestPayload:
    """Полный набор данных для одного daily digest."""

    window_start_utc: datetime
    window_end_utc: datetime
    alerts_warning_count: int
    alerts_stop_count: int
    top_ads_by_spend: list[TopAdRow] = field(default_factory=list)
    disable_tasks_succeeded: int = 0
    disable_tasks_failed: int = 0
    active_offers_count: int = 0
    active_ads_count: int = 0
    total_spend_24h_usd: Decimal = Decimal("0")


async def _count_alerts_by_stage(
    engine: AsyncEngine,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[int, int]:
    """Считает алерты за окно отдельно по стадиям warning/stop.

    Использует partition pruning — alert_events партиционирована по created_at.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE stage = 'warning') AS w,
                        COUNT(*) FILTER (WHERE stage = 'stop')    AS s
                    FROM alert_events
                    WHERE created_at >= :start
                      AND created_at <  :end
                    """
                ),
                {"start": window_start, "end": window_end},
            )
        ).one()
    return int(row[0] or 0), int(row[1] or 0)


async def _count_disable_tasks(
    engine: AsyncEngine,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[int, int]:
    """Считает выполненные disable_tasks за окно.

    Фильтр по completed_at — таски берутся только те, что фактически
    завершились в окне (а не были созданы в нём).
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'succeeded') AS ok,
                        COUNT(*) FILTER (WHERE status = 'failed')    AS fail
                    FROM task_queue
                    WHERE task_type = 'disable'
                      AND completed_at IS NOT NULL
                      AND completed_at >= :start
                      AND completed_at <  :end
                    """
                ),
                {"start": window_start, "end": window_end},
            )
        ).one()
    return int(row[0] or 0), int(row[1] or 0)


async def _count_active_offers(engine: AsyncEngine) -> int:
    """Сколько офферов помечены активными (is_active=true)."""
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT COUNT(*) FROM offers WHERE is_active = TRUE"))).one()
    return int(row[0] or 0)


async def _count_active_ads_normal(engine: AsyncEngine) -> int:
    """Активные объявления (is_active=true) в состоянии 'normal', живые за 7 дней.

    Фильтр по `last_seen_at` отсекает старые объявления, которых уже нет
    в Ads Manager (observer перестал их видеть): без этого фильтра счётчик
    рос бы вечно — `is_active=TRUE` отстаёт от реального отключения.

    Считаем по fb_ads, у которых либо нет записи в ad_alert_state, либо она
    'normal'. Это удобный косвенный показатель «живых» объявлений без open алертов.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM fb_ads a
                    LEFT JOIN ad_alert_state s ON s.ad_id = a.id
                    WHERE a.is_active = TRUE
                      AND a.last_seen_at >= NOW() - INTERVAL '7 days'
                      AND COALESCE(s.alert_state, 'normal') = 'normal'
                    """
                )
            )
        ).one()
    return int(row[0] or 0)


async def _top_ads_and_total_spend(
    engine: AsyncEngine,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int = 5,
) -> tuple[list[TopAdRow], Decimal]:
    """Топ-N ad по последнему spend за окно + общий 24h spend.

    Логика:
    - на ad_metrics берётся самая поздняя строка в окне (LATERAL/DISTINCT ON)
      — это «снимок» по объявлению на конец окна;
    - суммарный spend = SUM(этих последних snapshot'ов).

    ⚠️ ad_metrics partitioned by cycle_ts — обязательно указываем границы окна,
    иначе сканируются все партиции.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    WITH last_metrics AS (
                        SELECT DISTINCT ON (m.ad_id)
                            m.ad_id,
                            m.spend,
                            m.clicks,
                            m.leads,
                            m.cpc,
                            m.cost_per_lead
                        FROM ad_metrics m
                        WHERE m.cycle_ts >= :start
                          AND m.cycle_ts <  :end
                        ORDER BY m.ad_id, m.cycle_ts DESC
                    )
                    SELECT
                        a.id, a.fb_ad_id, a.ad_name,
                        o.code AS offer_code,
                        lm.spend, lm.clicks, lm.leads,
                        lm.cpc, lm.cost_per_lead
                    FROM last_metrics lm
                    JOIN fb_ads a       ON a.id = lm.ad_id
                    JOIN fb_adsets ads  ON ads.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = ads.campaign_id
                    LEFT JOIN offers o  ON o.id = c.offer_id
                    WHERE lm.spend IS NOT NULL
                    ORDER BY lm.spend DESC NULLS LAST
                    LIMIT :limit
                    """
                ),
                {"start": window_start, "end": window_end, "limit": int(limit)},
            )
        ).all()

        total_row = (
            await conn.execute(
                text(
                    """
                    WITH last_metrics AS (
                        SELECT DISTINCT ON (m.ad_id)
                            m.ad_id,
                            m.spend
                        FROM ad_metrics m
                        WHERE m.cycle_ts >= :start
                          AND m.cycle_ts <  :end
                        ORDER BY m.ad_id, m.cycle_ts DESC
                    )
                    SELECT COALESCE(SUM(spend), 0)
                    FROM last_metrics
                    """
                ),
                {"start": window_start, "end": window_end},
            )
        ).one()

    top_rows = [
        TopAdRow(
            ad_id=row[0],
            fb_ad_id=str(row[1]),
            ad_name=str(row[2] or ""),
            offer_code=str(row[3]) if row[3] else None,
            spend_usd=Decimal(str(row[4] or 0)),
            clicks=int(row[5] or 0),
            leads=int(row[6] or 0),
            cpc=Decimal(str(row[7])) if row[7] is not None else None,
            cost_per_lead=Decimal(str(row[8])) if row[8] is not None else None,
        )
        for row in rows
    ]
    total = Decimal(str(total_row[0] or 0))
    return top_rows, total


async def build_digest(
    engine: AsyncEngine,
    *,
    day_start_utc: datetime,
    window_hours: int = 24,
    top_limit: int = 5,
) -> DigestPayload:
    """Собирает агрегированный payload для daily digest.

    day_start_utc — конец окна (момент, в который мы строим digest); окно
    идёт назад на `window_hours` часов. Удобно для тестов: передаём явное
    «сейчас», не зависим от system clock.
    """
    if day_start_utc.tzinfo is None:
        raise ValueError("day_start_utc должен быть timezone-aware")

    window_end = day_start_utc
    window_start = window_end - timedelta(hours=window_hours)

    warn_cnt, stop_cnt = await _count_alerts_by_stage(
        engine, window_start=window_start, window_end=window_end
    )
    ok_cnt, fail_cnt = await _count_disable_tasks(
        engine, window_start=window_start, window_end=window_end
    )
    offers_cnt = await _count_active_offers(engine)
    ads_cnt = await _count_active_ads_normal(engine)
    top_ads, total_spend = await _top_ads_and_total_spend(
        engine,
        window_start=window_start,
        window_end=window_end,
        limit=top_limit,
    )

    return DigestPayload(
        window_start_utc=window_start,
        window_end_utc=window_end,
        alerts_warning_count=warn_cnt,
        alerts_stop_count=stop_cnt,
        top_ads_by_spend=top_ads,
        disable_tasks_succeeded=ok_cnt,
        disable_tasks_failed=fail_cnt,
        active_offers_count=offers_cnt,
        active_ads_count=ads_cnt,
        total_spend_24h_usd=total_spend,
    )


__all__ = [
    "DigestPayload",
    "TopAdRow",
    "build_digest",
]
