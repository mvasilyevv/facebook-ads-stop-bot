# -*- coding: utf-8 -*-
"""Роутер desktop ads-actions: snooze + bulk-snooze.

Endpoints (с prefix /api от auto-discovery):
    POST /dashboard/ads/{fb_ad_id}/snooze — отложить алерты по объявлению
    POST /dashboard/ads/bulk-snooze       — массовый снуз (partial-failure)

Snooze = ad_alert_state.snoozed_until = now + minutes. Observer перестаёт
ре-алертить пока snoozed_until > NOW(). Это НЕ отключает рекламу — только
заглушает уведомления (см. core/observer/state_machine.py).

Логика портирована из tma.py (tma_snooze_ad). Desktop /dashboard роутеры
открыты (без Bearer-guard), как остальной desktop UI; провенанс — в логах.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.ads_actions import (
    BULK_SNOOZE_MAX_IDS,
    BulkSnoozeIn,
    BulkSnoozeResultOut,
    SnoozeIn,
    SnoozeResultOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


# ─────────────────── POST /dashboard/ads/{fb_ad_id}/snooze ───────────────────


@router.post("/dashboard/ads/{fb_ad_id}/snooze", response_model=SnoozeResultOut)
async def snooze_ad(
    fb_ad_id: str,
    body: SnoozeIn,
    engine: DepEngine,
) -> SnoozeResultOut:
    """Снуз одного объявления: ad_alert_state.snoozed_until = now + minutes.

    404 — объявления нет в fb_ads. 409 — у ad нет строки состояния (нечего снузить).
    422 — ад в normal (нет активного инцидента): снуз задуман «не спамить алертами по
    активному инциденту», а не «выключить авто-стоп». Снуз на normal-аде заглушил бы
    будущий STOP до конца окна — money-дыра (MID-2). Зеркало tma_snooze_ad.
    """
    until = datetime.now(timezone.utc) + timedelta(minutes=body.minutes)
    async with engine.begin() as conn:
        ad_row = (
            await conn.execute(
                text("SELECT id FROM fb_ads WHERE fb_ad_id = :fid LIMIT 1"),
                {"fid": fb_ad_id},
            )
        ).first()
        if ad_row is None:
            raise HTTPException(status_code=404, detail="Объявление не найдено")
        # UPDATE только при активном инциденте: снуз на normal-аде запрещён (money-дыра).
        result = await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET snoozed_until = :until, updated_at = NOW()
                WHERE ad_id = :ad_id
                  AND alert_state != 'normal'
                """
            ),
            {"until": until, "ad_id": ad_row.id},
        )
        if (result.rowcount or 0) == 0:
            # Различаем «нет строки состояния» (409) и «ад в normal» (422).
            state_row = (
                await conn.execute(
                    text("SELECT alert_state FROM ad_alert_state WHERE ad_id = :ad_id"),
                    {"ad_id": ad_row.id},
                )
            ).first()
            if state_row is None:
                raise HTTPException(
                    status_code=409, detail="У объявления нет состояния алерта — нечего снузить"
                )
            raise HTTPException(
                status_code=422,
                detail="Нельзя снузить объявление в состоянии normal — нет активного инцидента",
            )
    logger.info("dashboard snooze: ad=%s до %s (%d мин)", fb_ad_id, until, body.minutes)
    return SnoozeResultOut(ok=True, fb_ad_id=fb_ad_id, snoozed_until=until.isoformat())


# ─────────────────────── POST /dashboard/ads/bulk-snooze ─────────────────────


@router.post("/dashboard/ads/bulk-snooze", response_model=BulkSnoozeResultOut)
async def bulk_snooze_ads(
    body: BulkSnoozeIn,
    engine: DepEngine,
) -> BulkSnoozeResultOut:
    """Массовый снуз с partial-failure (HTTP 200).

    Один общий snoozed_until для всего batch. snoozed — fb_ad_id успешно снузленных;
    failed — ad не найден (no_ad) или нет строки состояния (no_alert_state).

    Реализация одним UPDATE через unnest+CTE: множественный UPDATE атомарен в одной
    транзакции, классификация неуспешных — отдельным LEFT JOIN-проходом по тому же
    списку (без N round-trip'ов на ad).

    422 — только на валидации входа (пустой список / превышение cap).
    """
    if len(body.fb_ad_ids) > BULK_SNOOZE_MAX_IDS:
        raise HTTPException(
            status_code=422,
            detail=f"Слишком большой batch: {len(body.fb_ad_ids)} > {BULK_SNOOZE_MAX_IDS}",
        )

    # Дедуп + чистка пустых, сохраняя порядок.
    seen: set[str] = set()
    unique_ids: list[str] = []
    for raw in body.fb_ad_ids:
        fid = (raw or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        unique_ids.append(fid)

    until = datetime.now(timezone.utc) + timedelta(minutes=body.minutes)

    async with engine.begin() as conn:
        # UPDATE снузит только ad с активным инцидентом (state != normal). Снуз на
        # normal-аде запрещён — заглушил бы будущий STOP (money-дыра, MID-2).
        updated_rows = (
            await conn.execute(
                text(
                    """
                    UPDATE ad_alert_state s
                    SET snoozed_until = :until, updated_at = NOW()
                    FROM fb_ads a
                    WHERE s.ad_id = a.id
                      AND a.fb_ad_id = ANY(CAST(:ids AS text[]))
                      AND s.alert_state != 'normal'
                    RETURNING a.fb_ad_id AS fb_ad_id
                    """
                ),
                {"until": until, "ids": unique_ids},
            )
        ).all()
        snoozed = [r.fb_ad_id for r in updated_rows]

        # Классификация неуспешных: для каждого запрошенного id — есть ли ad, есть ли
        # строка состояния, в normal ли она. Один проход unnest+LEFT JOIN.
        snoozed_set = set(snoozed)
        leftover = [fid for fid in unique_ids if fid not in snoozed_set]
        failed: list[dict] = []
        if leftover:
            class_rows = (
                await conn.execute(
                    text(
                        """
                        SELECT req.fid AS fid,
                               (a.id IS NOT NULL) AS ad_exists,
                               (s.ad_id IS NOT NULL) AS has_state,
                               s.alert_state AS alert_state
                        FROM unnest(CAST(:ids AS text[])) AS req(fid)
                        LEFT JOIN fb_ads a ON a.fb_ad_id = req.fid
                        LEFT JOIN ad_alert_state s ON s.ad_id = a.id
                        """
                    ),
                    {"ids": leftover},
                )
            ).all()
            for r in class_rows:
                if not r.ad_exists:
                    reason = "no_ad"
                elif not r.has_state:
                    reason = "no_alert_state"
                else:
                    # Строка состояния есть, но ад в normal — снуз запрещён.
                    reason = "normal_state"
                failed.append({"fb_ad_id": r.fid, "reason": reason})

    logger.info(
        "dashboard bulk-snooze: до %s (%d мин) snoozed=%d failed=%d",
        until,
        body.minutes,
        len(snoozed),
        len(failed),
    )
    return BulkSnoozeResultOut(
        snoozed_until=until.isoformat(),
        snoozed=snoozed,
        failed=failed,
    )
