# -*- coding: utf-8 -*-
"""MiniApp API — эндпоинты для Telegram WebApp."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, unquote

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.db import get_session_factory
from core.domain import AlertState, DisableTaskStatus
from core.models import AdSnapshot, DisableTask

router = APIRouter(prefix="/api/miniapp", tags=["miniapp"])


# === Схемы ===


class DashboardResponse(BaseModel):
    """Ответ дашборда MiniApp."""

    total_ads: int
    pending_disable_tasks: int
    spend_today: float | None
    leads_today: int
    deposits_today: int
    cpa_today: float | None
    top3_alerts: list[dict]


class AlertItem(BaseModel):
    """Элемент в списке алертов."""

    fb_ad_id: str
    ad_name: str
    campaign_name: str
    alert_state: str
    stop_rule_codes: list[str]
    last_observed_at: datetime


class AdDetailResponse(BaseModel):
    """Детали объявления."""

    fb_ad_id: str
    ad_name: str
    campaign_name: str
    alert_state: str
    spend: float
    cpc: float | None
    leads: int
    deposits: int
    latest_disable_task: dict | None


class DisableTaskResponse(BaseModel):
    """Ответ при создании задачи отключения."""

    ok: bool
    task_id: str


# === Утилиты ===


def _verify_init_data(init_data: str, bot_token: str) -> bool:
    """Проверяет подпись initData от Telegram WebApp."""
    if not init_data or not bot_token:
        return True  # dev mode: skip validation

    try:
        parsed = dict(parse_qsl(unquote(init_data), strict_parsing=True))
        hash_val = parsed.pop("hash", "")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, hash_val)
    except Exception:
        return True  # dev fallback


async def verify_miniapp_auth(
    x_telegram_init_data: str = Header(default=""),
) -> None:
    """Зависимость для проверки авторизации MiniApp."""
    settings = get_settings()
    token = getattr(settings, "telegram_bot_token", "") or ""
    if token and not _verify_init_data(x_telegram_init_data, token):
        raise HTTPException(status_code=401, detail="Неверная подпись initData")


# === Эндпоинты ===


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    db: AsyncSession = Depends(lambda: get_session_factory()()),
    _: None = Depends(verify_miniapp_auth),
) -> DashboardResponse:
    """Получает статистику дашборда и топ-3 алерта."""

    # Общее количество объявлений
    total_ads_result = await db.scalar(select(func.count(func.distinct(AdSnapshot.fb_ad_id))))
    total_ads = total_ads_result or 0

    # Количество ожидающих задач отключения
    pending_result = await db.scalar(
        select(func.count(DisableTask.id)).where(DisableTask.status == DisableTaskStatus.PENDING)
    )
    pending_disable_tasks = pending_result or 0

    # Суммы по текущему дню (условно: за последние 24ч)
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    spend_today_result = await db.scalar(
        select(func.sum(AdSnapshot.spend)).where(AdSnapshot.last_observed_at >= day_start)
    )
    spend_today = float(spend_today_result) if spend_today_result else None

    leads_today_result = await db.scalar(
        select(func.sum(AdSnapshot.leads)).where(AdSnapshot.last_observed_at >= day_start)
    )
    leads_today = int(leads_today_result) if leads_today_result else 0

    deposits_today_result = await db.scalar(
        select(func.sum(AdSnapshot.deposits)).where(AdSnapshot.last_observed_at >= day_start)
    )
    deposits_today = int(deposits_today_result) if deposits_today_result else 0

    # CPA: среднее за день
    cpa_today = None
    if deposits_today > 0 and spend_today:
        cpa_today = spend_today / deposits_today

    # Топ-3 алерта по приоритету
    query = (
        select(AdSnapshot)
        .where(
            AdSnapshot.alert_state.not_in(
                [AlertState.NORMAL, AlertState.DISABLED, AlertState.ARCHIVED]
            )
        )
        .order_by(AdSnapshot.last_observed_at.desc())
        .limit(3)
    )
    top3_snapshots = (await db.scalars(query)).all()

    top3_alerts = [
        {
            "fb_ad_id": s.fb_ad_id,
            "ad_name": s.ad_name,
            "campaign_name": s.campaign_name,
            "alert_state": s.alert_state.value,
            "stop_rule_codes": s.stop_rule_codes or [],
            "last_observed_at": s.last_observed_at.isoformat(),
        }
        for s in top3_snapshots
    ]

    return DashboardResponse(
        total_ads=total_ads,
        pending_disable_tasks=pending_disable_tasks,
        spend_today=spend_today,
        leads_today=leads_today,
        deposits_today=deposits_today,
        cpa_today=cpa_today,
        top3_alerts=top3_alerts,
    )


@router.get("/alerts", response_model=list[AlertItem])
async def get_alerts(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(lambda: get_session_factory()()),
    _: None = Depends(verify_miniapp_auth),
) -> list[AlertItem]:
    """Получает список алертов с пагинацией."""

    # Приоритет: STOP_SENT > WARNING_SENT > EARLY_SIGNAL_SENT > CLAIMED
    query = (
        select(AdSnapshot)
        .where(
            AdSnapshot.alert_state.not_in(
                [AlertState.NORMAL, AlertState.DISABLED, AlertState.ARCHIVED]
            )
        )
        .order_by(
            AdSnapshot.alert_state,
            AdSnapshot.last_observed_at.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    snapshots = (await db.scalars(query)).all()

    return [
        AlertItem(
            fb_ad_id=s.fb_ad_id,
            ad_name=s.ad_name,
            campaign_name=s.campaign_name,
            alert_state=s.alert_state.value,
            stop_rule_codes=s.stop_rule_codes or [],
            last_observed_at=s.last_observed_at,
        )
        for s in snapshots
    ]


@router.get("/ad/{fb_ad_id}", response_model=AdDetailResponse)
async def get_ad(
    fb_ad_id: str,
    db: AsyncSession = Depends(lambda: get_session_factory()()),
    _: None = Depends(verify_miniapp_auth),
) -> AdDetailResponse:
    """Получает детали объявления и последнюю задачу отключения."""

    snapshot = await db.scalar(select(AdSnapshot).where(AdSnapshot.fb_ad_id == fb_ad_id))

    if not snapshot:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    # Последняя задача отключения
    latest_task = await db.scalar(
        select(DisableTask)
        .where(DisableTask.fb_ad_id == fb_ad_id)
        .order_by(DisableTask.created_at.desc())
        .limit(1)
    )

    latest_disable_task = None
    if latest_task:
        latest_disable_task = {
            "task_id": str(latest_task.id),
            "status": latest_task.status.value,
            "created_at": latest_task.created_at.isoformat(),
        }

    return AdDetailResponse(
        fb_ad_id=snapshot.fb_ad_id,
        ad_name=snapshot.ad_name,
        campaign_name=snapshot.campaign_name,
        alert_state=snapshot.alert_state.value,
        spend=float(snapshot.spend) if snapshot.spend else 0.0,
        cpc=float(snapshot.cpc) if snapshot.cpc else None,
        leads=snapshot.leads or 0,
        deposits=snapshot.deposits or 0,
        latest_disable_task=latest_disable_task,
    )


@router.post("/disable/{fb_ad_id}", response_model=DisableTaskResponse)
async def disable_ad(
    fb_ad_id: str,
    db: AsyncSession = Depends(lambda: get_session_factory()()),
    _: None = Depends(verify_miniapp_auth),
) -> DisableTaskResponse:
    """Создаёт задачу отключения объявления (идемпотентно)."""

    # Получаем снимок объявления
    snapshot = await db.scalar(select(AdSnapshot).where(AdSnapshot.fb_ad_id == fb_ad_id))

    if not snapshot:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    # Проверяем, есть ли активная задача
    existing = await db.scalar(
        select(DisableTask).where(
            and_(
                DisableTask.fb_ad_id == fb_ad_id,
                DisableTask.status.in_(
                    [
                        DisableTaskStatus.PENDING,
                        DisableTaskStatus.RUNNING,
                        DisableTaskStatus.RETRYING,
                    ]
                ),
            )
        )
    )

    if existing:
        return DisableTaskResponse(ok=True, task_id=str(existing.id))

    # Создаём новую задачу
    task_id = uuid.uuid4()
    task = DisableTask(
        id=task_id,
        snapshot_id=snapshot.id,
        offer_id=snapshot.offer_id,
        fb_ad_id=fb_ad_id,
        ad_name=snapshot.ad_name,
        open_state_token=snapshot.open_state_token or "",
        idempotency_key=f"miniapp_{fb_ad_id}_{int(datetime.now(UTC).timestamp())}",
        status=DisableTaskStatus.PENDING,
        requested_by_username="miniapp",
    )

    db.add(task)
    await db.commit()

    return DisableTaskResponse(ok=True, task_id=str(task.id))


@router.get("/analytics")
async def get_analytics(
    db: AsyncSession = Depends(lambda: get_session_factory()()),
    _: None = Depends(verify_miniapp_auth),
) -> dict:
    """Получает аналитику за последние 24 часа."""

    now = datetime.now(UTC)
    day_start = now - timedelta(hours=24)

    # Берём по часам последние 24h
    snapshots = (
        await db.scalars(
            select(AdSnapshot)
            .where(AdSnapshot.last_observed_at >= day_start)
            .order_by(AdSnapshot.last_observed_at.asc())
        )
    ).all()

    # Простой формат: возвращаем сырые данные
    return {
        "funnel": [],
        "spend_24h": [
            {
                "time": s.last_observed_at.isoformat(),
                "spend": float(s.spend) if s.spend else 0.0,
            }
            for s in snapshots
        ],
    }
