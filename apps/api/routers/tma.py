# -*- coding: utf-8 -*-
"""Роутер Telegram Mini App (TMA) — аутентификация и сессия."""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db, require_tma_session
from core.auth.tma import InvalidInitDataError, issue_session_token, validate_init_data
from core.config import get_settings
from core.meta_api.queue import approve_draft_task, cancel_draft_task
from core.models import MetaApiMutationTask, TelegramRecipient, TelegramSettings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tma", tags=["tma"])


class AuthBody(BaseModel):
    """Тело запроса аутентификации через initData."""

    init_data: str


class AuthResponse(BaseModel):
    """Ответ с сессионным токеном."""

    token: str
    expires_in: int
    role: str


async def _load_telegram_settings(db: AsyncSession) -> TelegramSettings | None:
    """Загружает настройки Telegram (синглтон) из БД."""
    result = await db.execute(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    return result.scalar_one_or_none()


@router.post("/auth", response_model=AuthResponse)
async def tma_auth(body: AuthBody, db: AsyncSession = Depends(get_db)) -> AuthResponse:
    """Аутентификация через Telegram initData.

    Проверяет подпись Telegram, определяет роль пользователя (owner/recipient)
    и выдаёт сессионный токен.
    """
    settings = get_settings()

    # Валидация initData по алгоритму HMAC Telegram WebApp
    try:
        data = validate_init_data(
            body.init_data,
            settings.telegram_bot_token,
            max_age_seconds=settings.tma_session_ttl_seconds,
        )
    except InvalidInitDataError as exc:
        logger.warning("Неверный initData при TMA-аутентификации: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = data.get("user", {})
    tg_user_id = str(user.get("id", ""))
    if not tg_user_id:
        raise HTTPException(status_code=400, detail="Поле user.id отсутствует в initData")

    # Определяем роль пользователя
    tg_settings = await _load_telegram_settings(db)
    role: str | None = None

    if tg_settings and tg_settings.owner_telegram_user_id == tg_user_id:
        role = "owner"
    else:
        # Проверяем получателей
        result = await db.execute(
            select(TelegramRecipient).where(
                TelegramRecipient.telegram_user_id == tg_user_id,
                TelegramRecipient.is_active.is_(True),
            )
        )
        recipient = result.scalar_one_or_none()
        if recipient:
            role = "recipient"

    if role is None:
        raise HTTPException(status_code=403, detail="Доступ запрещён")

    token = issue_session_token(tg_user_id, settings.tma_session_ttl_seconds, settings.api_key)
    return AuthResponse(token=token, expires_in=settings.tma_session_ttl_seconds, role=role)


@router.get("/me")
async def tma_me(request: Request, _: None = Depends(require_tma_session)) -> dict:
    """Возвращает данные текущего TMA-сессии."""
    return {
        "telegram_user_id": request.state.tma_user_id,
        "role": getattr(request.state, "tma_role", None),
    }


# ---------------------------------------------------------------------------
# Pydantic-модели для ad-действий
# ---------------------------------------------------------------------------


class AdDetailResponse(BaseModel):
    """Зеркало AdDetailDTO."""

    fb_ad_id: str
    ad_name: str | None
    campaign_name: str | None
    adset_name: str | None
    state: str
    account_id: str | None
    metrics: dict
    snooze_until: datetime | None
    recent_alerts: list[dict]
    can_open_in_ads_manager: bool


class DisableRequest(BaseModel):
    """Тело запроса отключения объявления."""

    reason: str | None = None


class SnoozeRequest(BaseModel):
    """Тело запроса паузы объявления."""

    minutes: int = Field(ge=5, le=720, default=30)


# ---------------------------------------------------------------------------
# Вспомогательная функция: извлечь actor_username из state
# ---------------------------------------------------------------------------


def _get_actor_username(request: Request) -> str | None:
    """Пробует достать username из TMA-payload (если есть)."""
    return getattr(request.state, "tma_username", None)


# ---------------------------------------------------------------------------
# Эндпоинты объявлений
# ---------------------------------------------------------------------------


@router.get("/ads/{fb_ad_id}", response_model=AdDetailResponse)
async def tma_get_ad(
    fb_ad_id: str,
    request: Request,
    _: None = Depends(require_tma_session),
) -> AdDetailResponse:
    """Детальная информация об объявлении."""
    from core.ads.actions import AdNotFoundError, get_ad_detail

    try:
        dto = await get_ad_detail(fb_ad_id=fb_ad_id)
    except AdNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        logger.exception("TMA get_ad_detail: ad=%s", fb_ad_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from None

    actor_id = request.state.tma_user_id
    logger.info("TMA action: get_detail ad=%s actor=%s", fb_ad_id, actor_id)
    return AdDetailResponse(**dto.__dict__)


@router.post("/ads/{fb_ad_id}/disable")
async def tma_disable_ad(
    fb_ad_id: str,
    body: DisableRequest,
    request: Request,
    _: None = Depends(require_tma_session),
) -> dict:
    """Отправить объявление на отключение."""
    from core.ads.actions import AdActionError, AdNotFoundError, disable_ad

    actor_id = request.state.tma_user_id
    logger.info("TMA action: disable ad=%s actor=%s", fb_ad_id, actor_id)
    try:
        result = await disable_ad(
            fb_ad_id=fb_ad_id,
            actor_telegram_user_id=actor_id,
            actor_username=_get_actor_username(request),
            reason=body.reason,
        )
    except AdNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AdActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.exception("TMA disable_ad: ad=%s", fb_ad_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from None

    return {"ok": True, **result}


@router.post("/ads/{fb_ad_id}/snooze")
async def tma_snooze_ad(
    fb_ad_id: str,
    body: SnoozeRequest,
    request: Request,
    _: None = Depends(require_tma_session),
) -> dict:
    """Поставить объявление на паузу (snooze)."""
    from core.ads.actions import AdActionError, AdNotFoundError, snooze_ad

    actor_id = request.state.tma_user_id
    logger.info("TMA action: snooze ad=%s actor=%s minutes=%s", fb_ad_id, actor_id, body.minutes)
    try:
        snoozed_until = await snooze_ad(
            fb_ad_id=fb_ad_id,
            minutes=body.minutes,
            actor_telegram_user_id=actor_id,
        )
    except AdNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AdActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.exception("TMA snooze_ad: ad=%s", fb_ad_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from None

    return {"ok": True, "snoozed_until": snoozed_until.isoformat()}


@router.post("/ads/{fb_ad_id}/claim")
async def tma_claim_ad(
    fb_ad_id: str,
    request: Request,
    _: None = Depends(require_tma_session),
) -> dict:
    """Взять объявление в работу (claim)."""
    from core.ads.actions import AdActionError, AdNotFoundError, claim_ad

    actor_id = request.state.tma_user_id
    logger.info("TMA action: claim ad=%s actor=%s", fb_ad_id, actor_id)
    try:
        await claim_ad(fb_ad_id=fb_ad_id, actor_telegram_user_id=actor_id)
    except AdNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AdActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.exception("TMA claim_ad: ad=%s", fb_ad_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from None

    return {"ok": True}


# ---------------------------------------------------------------------------
# Draft mutation tasks (Marketing API outbox)
# ---------------------------------------------------------------------------


class DraftTaskSummary(BaseModel):
    """Краткое представление DRAFT-задачи для списка."""

    id: str
    mutation_kind: str
    target_id: str
    ad_account_id: str
    status: str
    requested_by: str
    created_at: datetime
    summary: str = Field(default="", description="Человекочитаемое описание задачи")


class DraftTaskDetail(BaseModel):
    """Полная карточка DRAFT-задачи для экрана подтверждения."""

    id: str
    mutation_kind: str
    target_id: str
    ad_account_id: str
    status: str
    payload: dict[str, Any]
    requested_by: str
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime
    last_error: str | None
    attempt_count: int


class DraftRejectBody(BaseModel):
    """Тело запроса отмены DRAFT-задачи."""

    reason: str | None = Field(default=None, max_length=500)


def _require_owner_role(request: Request) -> str:
    """Бросает 403 если роль не owner. Возвращает username (или user_id если нет)."""
    role = getattr(request.state, "tma_role", None)
    if role != "owner":
        raise HTTPException(
            status_code=403,
            detail="Подтверждать draft-задачи может только владелец Telegram-контура",
        )
    return getattr(request.state, "tma_username", "") or request.state.tma_user_id


def _build_draft_summary(task: MetaApiMutationTask) -> str:
    """Строит короткую человекочитаемую строку по DRAFT-задаче в зависимости от kind."""
    payload = task.payload_json or {}
    kind = task.mutation_kind
    if kind == "set_budget":
        cents = payload.get("daily_budget_cents") or payload.get("lifetime_budget_cents") or 0
        kind_word = "дневной" if "daily_budget_cents" in payload else "lifetime"
        return f"Бюджет {task.target_id}: {kind_word} ${cents / 100:.2f}"
    if kind == "clone_campaign":
        deep = "deep" if payload.get("deep_copy") else "shallow"
        return f"Клон кампании {payload.get('source_campaign_id', task.target_id)} ({deep})"
    if kind == "bulk_pause":
        ids = payload.get("ad_ids") or []
        return f"Пауза {len(ids)} объявл. (фильтр: {payload.get('filter', {})})"
    if kind == "create_campaign":
        countries = payload.get("countries") or []
        return (
            f"Новая кампания {payload.get('offer_code', '?')} "
            f"({', '.join(countries) if countries else 'без гео'})"
        )
    return f"{kind} → {task.target_id or 'bulk'}"


@router.get("/draft-tasks", response_model=list[DraftTaskSummary])
async def tma_list_draft_tasks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_tma_session),
    status: str = Query(default="DRAFT", description="Статус (по умолчанию только DRAFT)"),
    kind: str | None = Query(default=None, description="mutation_kind фильтр"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[DraftTaskSummary]:
    """Список draft-задач для текущего TMA-юзера (только owner)."""
    _require_owner_role(request)

    stmt = (
        select(MetaApiMutationTask)
        .where(MetaApiMutationTask.status == status)
        .order_by(MetaApiMutationTask.created_at.desc())
        .limit(limit)
    )
    if kind:
        stmt = stmt.where(MetaApiMutationTask.mutation_kind == kind)

    result = await db.execute(stmt)
    tasks = list(result.scalars().all())

    return [
        DraftTaskSummary(
            id=str(t.id),
            mutation_kind=t.mutation_kind,
            target_id=t.target_id,
            ad_account_id=t.ad_account_id,
            status=t.status,
            requested_by=t.requested_by,
            created_at=t.created_at,
            summary=_build_draft_summary(t),
        )
        for t in tasks
    ]


@router.get("/draft-tasks/{task_id}", response_model=DraftTaskDetail)
async def tma_get_draft_task(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_tma_session),
) -> DraftTaskDetail:
    """Полная карточка draft-задачи."""
    _require_owner_role(request)

    try:
        task_uuid = _uuid.UUID(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Некорректный task_id") from exc

    task = await db.scalar(select(MetaApiMutationTask).where(MetaApiMutationTask.id == task_uuid))
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    return DraftTaskDetail(
        id=str(task.id),
        mutation_kind=task.mutation_kind,
        target_id=task.target_id,
        ad_account_id=task.ad_account_id,
        status=task.status,
        payload=task.payload_json or {},
        requested_by=task.requested_by,
        approved_by=task.approved_by,
        approved_at=task.approved_at,
        created_at=task.created_at,
        last_error=task.last_error,
        attempt_count=task.attempt_count,
    )


@router.post("/draft-tasks/{task_id}/confirm")
async def tma_confirm_draft_task(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_tma_session),
) -> dict:
    """Подтвердить DRAFT-задачу: DRAFT → PENDING."""
    approver = _require_owner_role(request)

    try:
        task_uuid = _uuid.UUID(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Некорректный task_id") from exc

    logger.info(
        "TMA action: confirm draft task=%s actor=%s",
        task_id,
        request.state.tma_user_id,
    )
    try:
        task = await approve_draft_task(db, task_id=task_uuid, approved_by=approver)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.exception("TMA approve_draft_task: task=%s", task_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from None

    return {"ok": True, "status": task.status, "approved_by": task.approved_by}


@router.post("/draft-tasks/{task_id}/reject")
async def tma_reject_draft_task(
    task_id: str,
    body: DraftRejectBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_tma_session),
) -> dict:
    """Отменить DRAFT-задачу: DRAFT → CANCELLED."""
    canceller = _require_owner_role(request)

    try:
        task_uuid = _uuid.UUID(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Некорректный task_id") from exc

    logger.info(
        "TMA action: reject draft task=%s actor=%s reason=%s",
        task_id,
        request.state.tma_user_id,
        body.reason or "",
    )
    try:
        task = await cancel_draft_task(
            db,
            task_id=task_uuid,
            cancelled_by=canceller,
            reason=body.reason or "",
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.exception("TMA cancel_draft_task: task=%s", task_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from None

    return {"ok": True, "status": task.status}
