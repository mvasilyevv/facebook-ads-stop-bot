# -*- coding: utf-8 -*-
"""Запросы к БД для daily digest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import DisableTaskStatus
from core.models import (
    AdMetricHistory,
    AlertEvent,
    DisableTask,
    FbAd,
    FbAdset,
    FbCampaign,
    Offer,
)


async def get_digest_data(
    session: AsyncSession,
    *,
    now: datetime,
    tz_name: str = "Europe/Moscow",
) -> dict:
    """Собирает данные для daily digest.

    Возвращает словарь с ключами:
      top_offers   — список dict (code, spend, leads, deps, spend_prev, delta_pct)
      wasted_alerts — количество алёртов, не завершившихся отключением
      new_offers    — список кодов новых офферов за вчера
      totals        — dict (spend, leads, deps) за вчера
      date_str      — строка "DD.MM.YYYY" за вчера (в локальном TZ)
    """
    # Определяем границы «вчера» в UTC.
    # now передаётся снаружи (удобно для тестов).
    try:
        import zoneinfo  # Python 3.9+

        local_tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        local_tz = timezone.utc

    # Преобразуем now к локальному TZ для вычисления суточных границ
    now_local = now.astimezone(local_tz)
    today_local = now_local.date()
    yesterday_local = today_local - timedelta(days=1)
    day_before_local = yesterday_local - timedelta(days=1)

    # Границы вчерашнего дня в UTC
    def _day_bounds_utc(d) -> tuple[datetime, datetime]:
        import datetime as _dt

        start_local = _dt.datetime(d.year, d.month, d.day, tzinfo=local_tz)
        end_local = start_local + timedelta(days=1)
        return start_local.astimezone(UTC), end_local.astimezone(UTC)

    yt_start, yt_end = _day_bounds_utc(yesterday_local)
    dbt_start, dbt_end = _day_bounds_utc(day_before_local)

    top_offers = await _top_offers_by_spend(session, yt_start, yt_end, dbt_start, dbt_end)
    wasted_alerts = await _count_wasted_alerts(session, yt_start, yt_end)
    new_offers = await _new_offers(session, yt_start, yt_end)
    totals = await _day_totals(session, yt_start, yt_end)

    return {
        "top_offers": top_offers,
        "wasted_alerts": wasted_alerts,
        "new_offers": new_offers,
        "totals": totals,
        "date_str": yesterday_local.strftime("%d.%m.%Y"),
    }


async def _top_offers_by_spend(
    session: AsyncSession,
    yt_start: datetime,
    yt_end: datetime,
    dbt_start: datetime,
    dbt_end: datetime,
    top_n: int = 3,
) -> list[dict]:
    """Топ-N офферов по spend за вчера с дельтой к позавчера."""

    async def _spend_per_offer(start: datetime, end: datetime) -> dict[str, dict]:
        """Spend/leads/deps по офферу за указанный период из AdMetricHistory."""
        # MAX per-ad per-day, затем SUM
        from sqlalchemy import Date as SqlDate
        from sqlalchemy import cast

        day_col = cast(AdMetricHistory.cycle_ts, SqlDate).label("day")
        subq = (
            select(
                AdMetricHistory.ad_id,
                day_col,
                func.max(AdMetricHistory.spend).label("spend"),
                func.max(AdMetricHistory.leads).label("leads"),
                func.max(AdMetricHistory.deposits).label("deps"),
            )
            .where(
                AdMetricHistory.cycle_ts >= start,
                AdMetricHistory.cycle_ts < end,
            )
            .group_by(AdMetricHistory.ad_id, day_col)
            .subquery()
        )

        q = (
            select(
                FbCampaign.offer_code,
                func.sum(subq.c.spend).label("spend"),
                func.sum(subq.c.leads).label("leads"),
                func.sum(subq.c.deps).label("deps"),
            )
            .join(FbAd, FbAd.id == subq.c.ad_id)
            .join(FbAdset, FbAd.adset_id == FbAdset.id)
            .join(FbCampaign, FbAdset.campaign_id == FbCampaign.id)
            .where(FbCampaign.offer_code.isnot(None))
            .group_by(FbCampaign.offer_code)
        )
        rows = (await session.execute(q)).all()
        return {
            row.offer_code: {
                "spend": Decimal(str(row.spend or 0)),
                "leads": int(row.leads or 0),
                "deps": int(row.deps or 0),
            }
            for row in rows
            if row.offer_code
        }

    yt_data = await _spend_per_offer(yt_start, yt_end)
    dbt_data = await _spend_per_offer(dbt_start, dbt_end)

    # Сортируем по spend вчера DESC и берём топ-N
    sorted_codes = sorted(yt_data.keys(), key=lambda c: yt_data[c]["spend"], reverse=True)[:top_n]

    result = []
    for code in sorted_codes:
        m = yt_data[code]
        prev_m = dbt_data.get(code, {})
        prev_spend = prev_m.get("spend", Decimal("0"))
        spend = m["spend"]

        # Дельта в % к предыдущему дню
        if prev_spend and prev_spend > 0:
            delta_pct = float(((spend - prev_spend) / prev_spend) * 100)
        else:
            delta_pct = None

        result.append(
            {
                "code": code,
                "spend": spend,
                "leads": m["leads"],
                "deps": m["deps"],
                "spend_prev": prev_spend,
                "delta_pct": delta_pct,
            }
        )

    return result


async def _count_wasted_alerts(
    session: AsyncSession,
    yt_start: datetime,
    yt_end: datetime,
) -> int:
    """Алёрты за вчера, которые не привели к реальному отключению.

    «Впустую» = state не дошёл до DISABLED (т.е. не было создано успешного DisableTask).
    Считаем AlertEvent за вчера, у которых нет соответствующего DisableTask с SUCCEEDED.
    Упрощённо: алёрты за вчера минус успешные отключения за вчера (разные периметры,
    но для дайджеста это достаточная оценка «сколько алёртов пользователь проигнорировал»).
    """
    total_alerts_q = select(func.count()).where(
        and_(
            AlertEvent.created_at >= yt_start,
            AlertEvent.created_at < yt_end,
        )
    )
    total_alerts = (await session.execute(total_alerts_q)).scalar() or 0

    # Успешные отключения за вчера
    success_q = select(func.count()).where(
        and_(
            DisableTask.created_at >= yt_start,
            DisableTask.created_at < yt_end,
            DisableTask.status == DisableTaskStatus.SUCCEEDED,
        )
    )
    succeeded = (await session.execute(success_q)).scalar() or 0

    # Алёрты «впустую» — не приведшие к отключению (не уходим в минус)
    return max(0, total_alerts - succeeded)


async def _new_offers(
    session: AsyncSession,
    yt_start: datetime,
    yt_end: datetime,
) -> list[str]:
    """Офферы, созданные за вчера."""
    q = select(Offer.code).where(
        and_(
            Offer.created_at >= yt_start,
            Offer.created_at < yt_end,
        )
    )
    rows = (await session.execute(q)).all()
    return [row[0] for row in rows]


async def _day_totals(
    session: AsyncSession,
    yt_start: datetime,
    yt_end: datetime,
) -> dict:
    """Суммарные spend/leads/deps за период из AdMetricHistory."""
    from sqlalchemy import Date as SqlDate
    from sqlalchemy import cast

    day_col = cast(AdMetricHistory.cycle_ts, SqlDate).label("day")
    subq = (
        select(
            AdMetricHistory.ad_id,
            day_col,
            func.max(AdMetricHistory.spend).label("spend"),
            func.max(AdMetricHistory.leads).label("leads"),
            func.max(AdMetricHistory.deposits).label("deps"),
        )
        .where(
            AdMetricHistory.cycle_ts >= yt_start,
            AdMetricHistory.cycle_ts < yt_end,
        )
        .group_by(AdMetricHistory.ad_id, day_col)
        .subquery()
    )

    q = select(
        func.sum(subq.c.spend).label("spend"),
        func.sum(subq.c.leads).label("leads"),
        func.sum(subq.c.deps).label("deps"),
    )
    row = (await session.execute(q)).one()

    return {
        "spend": Decimal(str(row.spend or 0)),
        "leads": int(row.leads or 0),
        "deps": int(row.deps or 0),
    }
