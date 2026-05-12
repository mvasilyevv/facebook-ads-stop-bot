from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.db import get_session_factory
from core.domain import AlertState
from core.models import (
    AdSnapshot,
    AlertEvent,
    AlertSnooze,
    DisableTask,
    DisableTaskStatus,
    FbAd,
    ObserverSettings,
)

logger = logging.getLogger(__name__)

_SNOOZE_MIN = 5
_SNOOZE_MAX = 720
_RECENT_ALERTS_LIMIT = 10


@dataclass
class AdDetailDTO:
    fb_ad_id: str
    ad_name: str | None
    campaign_name: str | None
    adset_name: str | None
    state: str  # AlertState.value
    account_id: str | None
    metrics: dict  # spend, leads, deposits, cpc, cpl, ctr, holds из снапшота
    snooze_until: datetime | None
    recent_alerts: list[dict] = field(
        default_factory=list
    )  # [{stage, state, created_at, reason_title}]
    can_open_in_ads_manager: bool = False


class AdNotFoundError(Exception):
    pass


class AdActionError(Exception):
    pass


async def get_ad_detail(*, fb_ad_id: str) -> AdDetailDTO:
    """Возвращает подробности об объявлении: метрики, состояние, историю алертов."""
    factory = get_session_factory()
    async with factory() as session:
        # Найти FbAd
        row = await session.execute(select(FbAd).where(FbAd.fb_ad_id == fb_ad_id))
        fb_ad = row.scalars().first()
        if fb_ad is None:
            raise AdNotFoundError(f"Объявление {fb_ad_id} не найдено")

        # AdSnapshot (один-к-одному через unique fb_ad_id)
        snap_row = await session.execute(
            select(AdSnapshot)
            .where(AdSnapshot.fb_ad_id == fb_ad_id)
            .order_by(AdSnapshot.last_observed_at.desc())
            .limit(1)
        )
        snap: AdSnapshot | None = snap_row.scalars().first()

        # Метрики
        metrics: dict = {}
        state_val = AlertState.NORMAL.value
        if snap is not None:
            state_val = snap.alert_state.value if snap.alert_state else AlertState.NORMAL.value
            metrics = {
                "spend": float(snap.spend) if snap.spend is not None else None,
                "leads": snap.leads,
                "deposits": snap.deposits,
                "cpc": float(snap.cpc) if snap.cpc is not None else None,
                "cpl": float(snap.cost_per_lead) if snap.cost_per_lead is not None else None,
                "ctr": float(snap.ctr) if snap.ctr is not None else None,
                "holds": snap.registrations,
            }

        # Имя кампании и адсета через связи fb_ad → adset → campaign
        campaign_name: str | None = None
        adset_name: str | None = None
        ad_name: str | None = fb_ad.ad_name or None
        try:
            await session.refresh(fb_ad, ["adset"])
            if fb_ad.adset:
                adset_name = fb_ad.adset.adset_name or None
                await session.refresh(fb_ad.adset, ["campaign"])
                if fb_ad.adset.campaign:
                    campaign_name = fb_ad.adset.campaign.campaign_name
        except Exception:
            pass

        # Последние N алертов
        alerts_rows = await session.execute(
            select(AlertEvent)
            .where(AlertEvent.ad_id == fb_ad.id)
            .order_by(AlertEvent.created_at.desc())
            .limit(_RECENT_ALERTS_LIMIT)
        )
        recent_alerts = [
            {
                "stage": e.stage.value if e.stage else None,
                "state": e.state.value if e.state else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "reason_title": e.reason_title,
            }
            for e in alerts_rows.scalars().all()
        ]

        # Активный снуз (максимальный snoozed_until, ещё не истёкший)
        now_utc = datetime.now(UTC)
        snooze_row = await session.execute(
            select(AlertSnooze)
            .where(AlertSnooze.fb_ad_id == fb_ad_id)
            .where(AlertSnooze.snoozed_until > now_utc)
            .order_by(AlertSnooze.snoozed_until.desc())
            .limit(1)
        )
        snooze_rec = snooze_row.scalars().first()
        snooze_until = snooze_rec.snoozed_until if snooze_rec else None

        # account_id из ObserverSettings
        obs_row = await session.execute(select(ObserverSettings).limit(1))
        obs = obs_row.scalars().first()
        account_id = obs.fb_account_id if obs and obs.fb_account_id else None
        can_open = account_id is not None

        logger.info("get_ad_detail: fb_ad_id=%s state=%s", fb_ad_id, state_val)
        return AdDetailDTO(
            fb_ad_id=fb_ad_id,
            ad_name=ad_name,
            campaign_name=campaign_name,
            adset_name=adset_name,
            state=state_val,
            account_id=account_id,
            metrics=metrics,
            snooze_until=snooze_until,
            recent_alerts=recent_alerts,
            can_open_in_ads_manager=can_open,
        )


async def disable_ad(
    *,
    fb_ad_id: str,
    actor_telegram_user_id: str,
    actor_username: str | None,
    reason: str | None = None,
) -> dict:
    """Создаёт DisableTask для объявления. При дубле idempotency_key возвращает существующую.

    Возвращает: {task_id, created_new, ad_name}
    """
    factory = get_session_factory()
    async with factory() as session:
        # Найти FbAd
        row = await session.execute(select(FbAd).where(FbAd.fb_ad_id == fb_ad_id))
        fb_ad = row.scalars().first()
        if fb_ad is None:
            raise AdNotFoundError(f"Объявление {fb_ad_id} не найдено")

        # Последний снапшот
        snap_row = await session.execute(
            select(AdSnapshot)
            .where(AdSnapshot.fb_ad_id == fb_ad_id)
            .order_by(AdSnapshot.last_observed_at.desc())
            .limit(1)
        )
        snap: AdSnapshot | None = snap_row.scalars().first()

        open_state_token = (
            snap.open_state_token if snap and snap.open_state_token else uuid.uuid4().hex
        )
        idempotency_key = f"disable:{fb_ad_id}:{open_state_token}"

        task = DisableTask(
            ad_id=fb_ad.id,
            snapshot_id=snap.id if snap else None,
            open_state_token=open_state_token,
            idempotency_key=idempotency_key,
            status=DisableTaskStatus.PENDING,
            requested_by_telegram_user_id=actor_telegram_user_id,
            requested_by_username=actor_username,
        )
        session.add(task)
        try:
            await session.commit()
            await session.refresh(task)
            logger.info(
                "disable_ad: создана DisableTask id=%s fb_ad_id=%s actor=%s",
                task.id,
                fb_ad_id,
                actor_telegram_user_id,
            )
            return {"task_id": str(task.id), "created_new": True, "ad_name": fb_ad.ad_name}
        except IntegrityError:
            await session.rollback()
            # Вернуть существующую задачу
            existing_row = await session.execute(
                select(DisableTask).where(DisableTask.idempotency_key == idempotency_key)
            )
            existing = existing_row.scalars().first()
            logger.info(
                "disable_ad: дубль idempotency_key=%s, возвращаю существующую task id=%s",
                idempotency_key,
                existing.id if existing else None,
            )
            return {
                "task_id": str(existing.id) if existing else None,
                "created_new": False,
                "ad_name": fb_ad.ad_name,
            }


async def snooze_ad(
    *,
    fb_ad_id: str,
    minutes: int,
    actor_telegram_user_id: str,
) -> datetime:
    """Создаёт AlertSnooze для объявления. minutes clamp'ится в [5, 720].

    Возвращает snoozed_until.
    """
    clamped = max(_SNOOZE_MIN, min(_SNOOZE_MAX, minutes))
    snoozed_until = datetime.now(UTC) + timedelta(minutes=clamped)

    factory = get_session_factory()
    async with factory() as session:
        snooze = AlertSnooze(
            fb_ad_id=fb_ad_id,
            snoozed_until=snoozed_until,
            created_by_telegram_user_id=actor_telegram_user_id,
        )
        session.add(snooze)
        await session.commit()

    logger.info(
        "snooze_ad: fb_ad_id=%s actor=%s minutes=%d snoozed_until=%s",
        fb_ad_id,
        actor_telegram_user_id,
        clamped,
        snoozed_until.isoformat(),
    )
    return snoozed_until


async def claim_ad(*, fb_ad_id: str, actor_telegram_user_id: str) -> None:
    """Переводит последний активный алерт объявления в состояние CLAIMED.

    Поднимает AdActionError если нет алерта в WARNING_SENT / STOP_SENT.
    """
    claimable = {AlertState.WARNING_SENT, AlertState.STOP_SENT}
    factory = get_session_factory()
    async with factory() as session:
        # Найти FbAd для получения ad.id
        row = await session.execute(select(FbAd).where(FbAd.fb_ad_id == fb_ad_id))
        fb_ad = row.scalars().first()
        if fb_ad is None:
            raise AdNotFoundError(f"Объявление {fb_ad_id} не найдено")

        # Последний AlertEvent в клеймируемом состоянии
        alert_row = await session.execute(
            select(AlertEvent)
            .where(AlertEvent.ad_id == fb_ad.id)
            .where(AlertEvent.state.in_(list(claimable)))
            .order_by(AlertEvent.created_at.desc())
            .limit(1)
        )
        alert: AlertEvent | None = alert_row.scalars().first()
        if alert is None:
            raise AdActionError("Нет активного алерта")

        alert.state = AlertState.CLAIMED
        await session.commit()

    logger.info(
        "claim_ad: fb_ad_id=%s alert_event_id=%s actor=%s → CLAIMED",
        fb_ad_id,
        alert.id,
        actor_telegram_user_id,
    )
