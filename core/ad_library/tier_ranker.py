# -*- coding: utf-8 -*-
"""Tier ranker — назначение S/A/B/C тиров на основе days_running + page_history + cluster_size."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


@dataclass
class TierEntry:
    """Один tier-результат для конкретного ad."""

    ad_archive_id: int
    tier: str  # S/A/B/C
    score: float
    reason: dict[str, Any]


def _days_running(started_running_on: date | None, now: datetime | None = None) -> int:
    """Сколько дней объявление активно (с момента started_running_on до now).

    Если started_running_on отсутствует — возвращает 0.
    """
    if not started_running_on:
        return 0
    now = now or datetime.now(timezone.utc)
    return max(0, (now.date() - started_running_on).days)


def compute_tier(
    *,
    days_running: int,
    page_history_count: int,
    cluster_size: int,
    classification_score: float,
) -> tuple[str, float, dict[str, Any]]:
    """Pure-функция расчёта tier.

    Логика:
    - S: 30+ дней running + 2+ креатива с одной страницы → proven winner
    - A: 14-30 дней + (page_history >= 2 ИЛИ cluster_size >= 3)
    - B: 7-14 дней
    - C: иначе

    Score 0..1 для упорядочивания внутри tier.
    classification_score — насколько ad релевантен запрошенному slot (от scanner.py).
    """
    reason: dict[str, Any] = {
        "days_running": days_running,
        "page_history_count": page_history_count,
        "cluster_size": cluster_size,
        "classification_score": round(classification_score, 4),
    }

    if days_running >= 30 and (page_history_count >= 2 or cluster_size >= 2):
        tier = "S"
        score = 0.9 + min(0.1, days_running / 1000.0)
    elif days_running >= 14 and (page_history_count >= 2 or cluster_size >= 3):
        tier = "A"
        score = 0.7 + min(0.2, days_running / 200.0)
    elif days_running >= 7:
        tier = "B"
        score = 0.4 + min(0.3, days_running / 50.0)
    else:
        tier = "C"
        score = 0.1 + classification_score * 0.3

    # Подкорректировать score по релевантности к slot
    score *= max(0.5, classification_score)
    score = max(0.0, min(1.0, score))
    reason["assigned_tier"] = tier
    return tier, score, reason


async def rank_scan(
    engine: AsyncEngine,
    *,
    scan_id: str,
) -> dict[str, int]:
    """Ранжирует все ads в данном scan'е и сохраняет tier-записи.

    Returns: {S, A, B, C} counts.
    """
    counts = {"S": 0, "A": 0, "B": 0, "C": 0}
    now = datetime.now(timezone.utc)

    async with engine.connect() as conn:
        # Все ads в scan'е + their per-page counts + (TODO) cluster_size
        ads = await conn.execute(
            text(
                """
                SELECT
                    a.ad_archive_id,
                    a.page_id,
                    a.started_running_on,
                    a.classification_score,
                    a.slot,
                    a.country
                FROM ad_library_snapshot s
                JOIN ad_library_ad a ON a.ad_archive_id = s.ad_archive_id
                WHERE s.scan_id = :sid
                """
            ),
            {"sid": scan_id},
        )
        rows = [
            (
                r[0],  # ad_archive_id
                r[1],  # page_id
                r[2],  # started_running_on
                float(r[3] or 0.0),  # classification_score
                r[4],  # slot
                r[5],  # country
            )
            for r in ads
        ]

        # Per-page history count (сколько ads с этой страницы есть В ad_library_ad — total)
        page_counts: dict[int, int] = {}
        if rows:
            unique_page_ids = list({r[1] for r in rows if r[1]})
            if unique_page_ids:
                pc_result = await conn.execute(
                    text(
                        """
                        SELECT page_id, COUNT(*)
                        FROM ad_library_ad
                        WHERE page_id = ANY(:ids)
                        GROUP BY page_id
                        """
                    ),
                    {"ids": unique_page_ids},
                )
                page_counts = {r[0]: r[1] for r in pc_result}

    # Сохраняем tier-записи
    async with engine.begin() as conn:
        for ad_archive_id, page_id, started, cs, _slot, _country in rows:
            page_history = page_counts.get(page_id, 1)
            cluster_size = 1  # MVP: пока без media-clustering. Будет в enricher v2.
            days = _days_running(started, now=now)
            tier, score, reason = compute_tier(
                days_running=days,
                page_history_count=page_history,
                cluster_size=cluster_size,
                classification_score=cs,
            )
            counts[tier] += 1
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_library_tier
                        (scan_id, ad_archive_id, tier, score, reason_json)
                    VALUES (:sid, :aid, :tier, :score, CAST(:reason AS JSONB))
                    ON CONFLICT (scan_id, ad_archive_id) DO UPDATE
                    SET tier = EXCLUDED.tier,
                        score = EXCLUDED.score,
                        reason_json = EXCLUDED.reason_json
                    """
                ),
                {
                    "sid": scan_id,
                    "aid": ad_archive_id,
                    "tier": tier,
                    "score": score,
                    "reason": json.dumps(reason),
                },
            )

            # S-tier автопромоут в winner_archive
            if tier == "S":
                await _auto_promote_winner(
                    conn,
                    ad_archive_id=ad_archive_id,
                    scan_id=scan_id,
                    tier=tier,
                    score=score,
                    reason=reason,
                )

    logger.info("Tier ranking for scan %s: %s", scan_id, counts)
    return counts


async def _auto_promote_winner(
    conn,
    *,
    ad_archive_id: int,
    scan_id: str,
    tier: str,
    score: float,
    reason: dict[str, Any],
) -> None:
    """S-tier ads автопромоутся в winner_archive (hold forever)."""
    # Нужны slot + country — берём из ad_library_ad
    ad_meta = (
        await conn.execute(
            text("SELECT slot, country FROM ad_library_ad WHERE ad_archive_id = :aid"),
            {"aid": ad_archive_id},
        )
    ).first()
    if not ad_meta:
        return

    slot, country = ad_meta
    reason_text = f"auto-promoted from scan {scan_id}: days_running={reason.get('days_running')}"
    await conn.execute(
        text(
            """
            INSERT INTO ad_library_winner_archive
                (ad_archive_id, original_scan_id, slot, country, tier, score, reason)
            VALUES (:aid, :sid, :slot, :country, :tier, :score, :reason)
            ON CONFLICT (ad_archive_id) DO UPDATE
            SET tier = EXCLUDED.tier,
                score = EXCLUDED.score,
                reason = EXCLUDED.reason
            """
        ),
        {
            "aid": ad_archive_id,
            "sid": scan_id,
            "slot": slot,
            "country": country,
            "tier": tier,
            "score": score,
            "reason": reason_text,
        },
    )
