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
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.dashboard.metric_aggregation import latest_per_ad_per_day_cte
from core.meta_api.account_tz import (
    resolve_account_currencies,
    resolve_cabinet_days,
)
from core.meta_api.identity import require_ad_account_id
from core.money import validated_currency_code


@dataclass(frozen=True)
class TopAdRow:
    """Одна строка топа объявлений по spend за окно."""

    ad_id: uuid.UUID
    fb_ad_id: str
    ad_name: str
    offer_code: str | None
    account_id: str
    currency: str
    spend: Decimal
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
    money_state: Literal["ready", "unavailable"] = "unavailable"
    money_account_id: str | None = None
    currency: str | None = None
    currency_observed_at: datetime | None = None
    money_issues: tuple[str, ...] = ()
    total_spend_window: Decimal | None = None


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
    """Считает завершённые задачи отключения рекламы за окно.

    Отключение идёт через единый Marketing API канал:
    task_type='meta_api_mutation' с mutation_kind='pause_ad'.

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
                    WHERE task_type = 'meta_api_mutation'
                      AND payload->>'mutation_kind' = 'pause_ad'
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
    account_id: str,
    currency: str,
    limit: int = 5,
) -> tuple[list[TopAdRow], Decimal]:
    """Топ-N и total внутри одного подтверждённого cabinet/currency scope.

    Логика:
    - топ-строки: latest-per-ad (DISTINCT ON ad_id) — для ранжирования по текущему spend;
    - total: DISTINCT ON (ad_id, day) → SUM дневных итогов (cabinet-день может охватывать
      несколько UTC-дней с cabinet-сбросом spend; наивный SUM снимков задваивает деньги).

    ⚠️ ad_metrics partitioned by cycle_ts — обязательно указываем границы окна,
    иначе сканируются все партиции.
    """
    canonical_account_id = require_ad_account_id(account_id)
    confirmed_currency = validated_currency_code(currency)
    if confirmed_currency is None:
        raise ValueError("digest currency must be confirmed")

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
                          AND m.currency = :currency
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
                    WHERE lm.spend > 0
                      AND c.ad_account_id = :account_id
                    ORDER BY lm.spend DESC NULLS LAST
                    LIMIT :limit
                    """
                ),
                {
                    "start": window_start,
                    "end": window_end,
                    "account_id": canonical_account_id,
                    "currency": confirmed_currency,
                    "limit": int(limit),
                },
            )
        ).all()

        # CRIT-1: spend кумулятивен и сбрасывается в cabinet-полночь.
        # Наивный DISTINCT ON (ad_id) берёт только последний snapshot → теряет день N-1.
        # Правильно: per-day CTE суммирует дневные итоги через посуточные сбросы.
        _total_cte = latest_per_ad_per_day_cte(
            cte_alias="per_ad_day",
            columns=("spend",),
            from_param="start",
            to_param="end",
            extra_select=", m.currency AS currency",
        )
        total_row = (
            await conn.execute(
                text(
                    f"WITH {_total_cte} "
                    "SELECT COALESCE(SUM(spend), 0) "
                    "FROM per_ad_day "
                    "WHERE ad_account_id = :account_id "
                    "AND timezone_known "
                    "AND currency = :currency"
                ),
                {
                    "start": window_start,
                    "end": window_end,
                    "account_id": canonical_account_id,
                    "currency": confirmed_currency,
                },
            )
        ).one()

    top_rows = [
        TopAdRow(
            ad_id=row[0],
            fb_ad_id=str(row[1]),
            ad_name=str(row[2] or ""),
            offer_code=str(row[3]) if row[3] else None,
            account_id=canonical_account_id,
            currency=confirmed_currency,
            spend=Decimal(str(row[4] or 0)),
            clicks=int(row[5] or 0),
            leads=int(row[6] or 0),
            cpc=Decimal(str(row[7])) if row[7] is not None else None,
            cost_per_lead=Decimal(str(row[8])) if row[8] is not None else None,
        )
        for row in rows
    ]
    total = Decimal(str(total_row[0] or 0))
    return top_rows, total


async def _money_scopes_with_evidence(
    engine: AsyncEngine,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[tuple[str, str | None], ...]:
    """Return exact cabinet/currency scopes that have spend evidence."""

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT DISTINCT campaign.ad_account_id, metric.currency
                    FROM ad_metrics AS metric
                    JOIN fb_ads AS ad ON ad.id = metric.ad_id
                    JOIN fb_adsets AS adset ON adset.id = ad.adset_id
                    JOIN fb_campaigns AS campaign ON campaign.id = adset.campaign_id
                    WHERE metric.cycle_ts >= :start
                      AND metric.cycle_ts < :end
                      AND metric.spend IS NOT NULL
                    ORDER BY campaign.ad_account_id, metric.currency NULLS FIRST
                    """
                ),
                {"start": window_start, "end": window_end},
            )
        ).all()
    return tuple(
        (
            require_ad_account_id(str(account_id)),
            validated_currency_code(currency),
        )
        for account_id, currency in rows
    )


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
    money_scopes = await _money_scopes_with_evidence(
        engine,
        window_start=window_start,
        window_end=window_end,
    )
    account_ids = tuple(sorted({account_id for account_id, _currency in money_scopes}))
    money_state: Literal["ready", "unavailable"] = "unavailable"
    money_account_id: str | None = None
    currency: str | None = None
    currency_observed_at: datetime | None = None
    money_issues: tuple[str, ...]
    top_ads: list[TopAdRow] = []
    total_spend: Decimal | None = None
    if not money_scopes:
        money_issues = ("Нет подтверждённых spend-снимков за окно",)
    elif any(scope_currency is None for _account_id, scope_currency in money_scopes):
        money_account_id = account_ids[0] if len(account_ids) == 1 else None
        money_issues = (
            "Денежные итоги скрыты: часть spend-снимков не имеет подтверждённой валюты",
        )
    elif len(money_scopes) != 1:
        money_account_id = account_ids[0] if len(account_ids) == 1 else None
        scope_kind = "несколько валют" if len(account_ids) == 1 else "несколько кабинетов"
        money_issues = (f"Денежные итоги скрыты: окно содержит {scope_kind}",)
    else:
        money_account_id, evidence_currency = money_scopes[0]
        assert evidence_currency is not None
        currency_resolution = await resolve_account_currencies(
            engine,
            account_ids=[money_account_id],
            now=window_end,
        )
        cabinet_days = await resolve_cabinet_days(
            engine,
            account_ids=[money_account_id],
            now=window_end,
        )
        current_currency = currency_resolution.currency
        currency_observed_at = currency_resolution.observed_at
        if currency_resolution.state != "single" or current_currency is None:
            money_issues = ("Денежные итоги скрыты: валюта кабинета не подтверждена",)
            currency_observed_at = None
        elif current_currency != evidence_currency:
            money_issues = ("Денежные итоги скрыты: валюта кабинета изменилась внутри окна",)
            currency_observed_at = None
        elif not cabinet_days.timezone_known:
            money_issues = ("Денежные итоги скрыты: граница суток кабинета не подтверждена",)
        else:
            currency = evidence_currency
            top_ads, total_spend = await _top_ads_and_total_spend(
                engine,
                window_start=window_start,
                window_end=window_end,
                account_id=money_account_id,
                currency=currency,
                limit=top_limit,
            )
            money_state = "ready"
            money_issues = ()

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
        money_state=money_state,
        money_account_id=money_account_id,
        currency=currency,
        currency_observed_at=currency_observed_at,
        money_issues=money_issues,
        total_spend_window=total_spend,
    )


__all__ = [
    "DigestPayload",
    "TopAdRow",
    "build_digest",
]
