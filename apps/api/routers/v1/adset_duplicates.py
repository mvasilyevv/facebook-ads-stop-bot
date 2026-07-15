# -*- coding: utf-8 -*-
"""Draft-first API быстрого дублирования выбранных ads из одного source adset."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Path

from apps.api.deps import DepEngine, DepRedis
from apps.api.routers.v1.schemas.adset_duplicates import (
    AdsetDuplicateDraftIn,
    AdsetDuplicateDraftOut,
    AdsetDuplicatePreviewIn,
    AdsetDuplicatePreviewOut,
    AdsetDuplicateStatusOut,
)
from core.adset_duplicates.delivery import (
    acquire_delivery_lock,
    delivery_lock_owned,
    release_delivery_lock,
    renew_delivery_lock,
)
from core.adset_duplicates.service import (
    AccountMetadata,
    AdsetDuplicateError,
    StoredDuplicatePreview,
    build_duplicate_preview,
    create_duplicate_draft,
    duplicate_draft_notification_delivered,
    fetch_account_metadata,
    get_duplicate_task,
    load_duplicate_source,
    load_stored_preview,
    mark_duplicate_draft_notification_delivered,
    mark_preview_consumed,
    render_draft_notification,
    save_stored_preview,
    serialize_duplicate_task,
)
from core.meta_api.audit import AuditedMetaApiClient
from core.meta_api.ownership import check_ad_ownership, load_owner_tag
from core.meta_api.queue import cancel_draft_task
from core.tasks.queue import DRAFT_TTL_SECONDS
from core.telegram.handlers.draft_confirm import draft_inline_keyboard
from core.telegram.worker_notify import notify_owners

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools/adset-duplicates", tags=["tools"])

_DELIVERY_LOCK_TTL_SECONDS = 30
_DELIVERY_LOCK_RENEW_SECONDS = 10.0
_DELIVERY_WAIT_ATTEMPTS = 50
_DELIVERY_WAIT_SECONDS = 0.1


def _http_error(exc: AdsetDuplicateError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


async def _renew_delivery_lease(
    redis: object,
    *,
    lock_key: str,
    owner_token: str,
    lost: asyncio.Event,
) -> None:
    """Продлевает lease до завершения Telegram I/O; lost запрещает опасный cancel."""
    while True:
        await asyncio.sleep(_DELIVERY_LOCK_RENEW_SECONDS)
        try:
            renewed = await renew_delivery_lock(
                redis,
                key=lock_key,
                owner_token=owner_token,
                ttl_seconds=_DELIVERY_LOCK_TTL_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("adset duplicate: delivery lease renewal упал")
            lost.set()
            return
        if not renewed:
            logger.error("adset duplicate: delivery lease потерян для %s", lock_key)
            lost.set()
            return


async def _consume_preview_best_effort(
    redis: object,
    *,
    preview_token: str,
    stored: StoredDuplicatePreview,
    task_id: int,
) -> None:
    """Preview mapping ускоряет retry, но PostgreSQL task marker остаётся source of truth."""
    try:
        await mark_preview_consumed(
            redis,
            preview_token=preview_token,
            stored=stored,
            task_id=task_id,
        )
    except Exception:
        logger.exception("adset duplicate: consumed marker не записан для task=%s", task_id)


async def _load_account_metadata(engine: object, account_id: str) -> AccountMetadata:
    """Один read-only Graph GET; недоступный Vision/Meta не заменяем ложными defaults."""
    client = AuditedMetaApiClient(
        engine=engine,
        initiated_by="api_adset_duplicate_preview",
        host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
    )
    try:
        await client.start()
        return await fetch_account_metadata(client, account_id)
    except AdsetDuplicateError:
        raise
    except Exception as exc:
        logger.warning("adset duplicate: account metadata недоступны", exc_info=True)
        raise AdsetDuplicateError(
            "Не удалось получить currency/timezone рекламного кабинета", status_code=503
        ) from exc
    finally:
        await client.close()


@router.post("/preview", response_model=AdsetDuplicatePreviewOut)
async def preview_adset_duplicate(
    body: AdsetDuplicatePreviewIn,
    engine: DepEngine,
    redis: DepRedis,
) -> AdsetDuplicatePreviewOut:
    """Read-only dry-run; сохраняет канонический план в Redis на 15 минут."""
    try:
        source = await load_duplicate_source(
            engine,
            source_ad_id=body.source_ad_id,
            selected_ad_ids=body.selected_ad_ids,
        )
        owner_tag = await load_owner_tag(engine)
        ownership = await check_ad_ownership(
            engine,
            source.source_ad_id,
            owner_tag=owner_tag,
        )
        if not ownership.allowed:
            raise AdsetDuplicateError(
                f"Исходное объявление вне owner-scope: {ownership.reason}",
                status_code=403,
            )
        account = await _load_account_metadata(engine, source.account_id)
        preview, task_params = build_duplicate_preview(
            source=source,
            account=account,
            campaign_count=body.campaign_count,
            adsets_per_campaign=body.adsets_per_campaign,
            budget_level=body.budget_level,
            daily_budget_cents=body.daily_budget_cents,
            requested_start_date=body.start_date,
            campaign_name_base=body.campaign_name_base,
            adset_name_base=body.adset_name_base,
            owner_tag=owner_tag,
        )
        public_preview = await save_stored_preview(
            redis,
            preview=preview,
            task_params=task_params,
            idempotency_token=body.idempotency_token,
        )
    except AdsetDuplicateError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        logger.exception("adset duplicate preview failed")
        raise HTTPException(status_code=503, detail="Не удалось сохранить preview") from exc
    return AdsetDuplicatePreviewOut.model_validate(public_preview)


@router.post("/draft", response_model=AdsetDuplicateDraftOut)
async def create_adset_duplicate_draft(
    body: AdsetDuplicateDraftIn,
    engine: DepEngine,
    redis: DepRedis,
) -> AdsetDuplicateDraftOut:
    """Создаёт только DRAFT; без успешной owner-нотификации сразу отменяет его."""
    task_id: int | None = None
    created = False
    stored = None
    try:
        stored = await load_stored_preview(redis, body.preview_token)
        if stored.consumed_task_id is not None:
            task_id, created = stored.consumed_task_id, False
        else:
            task_id, created = await create_duplicate_draft(engine, stored=stored)
    except AdsetDuplicateError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        if created and task_id is not None:
            await cancel_draft_task(
                engine,
                task_id=task_id,
                reason="preview consume failed before owner notification",
                admin_override=True,
            )
        logger.exception("adset duplicate draft creation failed")
        raise HTTPException(status_code=503, detail="Не удалось создать DRAFT") from exc

    assert task_id is not None and stored is not None
    task = await get_duplicate_task(engine, task_id)
    if task is None:
        raise HTTPException(status_code=500, detail="Созданный DRAFT не найден")

    # PostgreSQL marker общий для всех preview-token с одним idempotent task.
    if duplicate_draft_notification_delivered(task):
        await _consume_preview_best_effort(
            redis,
            preview_token=body.preview_token,
            stored=stored,
            task_id=task_id,
        )
    elif stored.consumed_task_id is None:
        lock_key = f"adset_duplicate:delivery:{task_id}"
        try:
            owner_token = await acquire_delivery_lock(
                redis,
                key=lock_key,
                ttl_seconds=_DELIVERY_LOCK_TTL_SECONDS,
            )
        except Exception as exc:
            # Без доказанного ownership нельзя отменять задачу: другой request мог
            # уже получить lease и доставлять те же idempotent кнопки.
            raise HTTPException(
                status_code=503,
                detail="Не удалось безопасно начать доставку подтверждения",
            ) from exc

        if owner_token is None:
            for _ in range(_DELIVERY_WAIT_ATTEMPTS):
                await asyncio.sleep(_DELIVERY_WAIT_SECONDS)
                refreshed = await load_stored_preview(redis, body.preview_token)
                current = await get_duplicate_task(engine, task_id)
                if current is None or current.status == "cancelled":
                    raise HTTPException(
                        status_code=503,
                        detail="Черновик отменён до доставки подтверждения",
                    )
                if refreshed.consumed_task_id == task_id or duplicate_draft_notification_delivered(
                    current
                ):
                    stored = refreshed
                    await _consume_preview_best_effort(
                        redis,
                        preview_token=body.preview_token,
                        stored=stored,
                        task_id=task_id,
                    )
                    break
            else:
                raise HTTPException(
                    status_code=409,
                    detail="Подтверждение ещё отправляется; повторите запрос через несколько секунд",
                )
        else:
            lock_lost = asyncio.Event()
            renewal_task = asyncio.create_task(
                _renew_delivery_lease(
                    redis,
                    lock_key=lock_key,
                    owner_token=owner_token,
                    lost=lock_lost,
                )
            )
            try:
                current = await get_duplicate_task(engine, task_id)
                if current is None:
                    raise HTTPException(status_code=500, detail="Созданный DRAFT не найден")
                if duplicate_draft_notification_delivered(current):
                    await _consume_preview_best_effort(
                        redis,
                        preview_token=body.preview_token,
                        stored=stored,
                        task_id=task_id,
                    )
                else:
                    if current.status != "draft":
                        raise HTTPException(
                            status_code=409,
                            detail=f"Черновик уже завершён со статусом {current.status}",
                        )
                    delivery_error: Exception | None = None
                    try:
                        delivered = await notify_owners(
                            engine,
                            redis,
                            category="adset_duplicate_draft",
                            text=render_draft_notification(task_id, stored),
                            reply_markup=draft_inline_keyboard(task_id),
                        )
                    except Exception as exc:
                        logger.exception(
                            "adset duplicate: owner notification упала для task=%s", task_id
                        )
                        delivery_error = exc
                        delivered = False

                    if delivered:
                        marker_error: Exception | None = None
                        try:
                            marked = await mark_duplicate_draft_notification_delivered(
                                engine,
                                task_id=task_id,
                            )
                        except Exception as exc:
                            logger.critical(
                                "Telegram доставлен, но task delivery marker упал для %s",
                                task_id,
                                exc_info=True,
                            )
                            marker_error = exc
                            marked = False
                        await _consume_preview_best_effort(
                            redis,
                            preview_token=body.preview_token,
                            stored=stored,
                            task_id=task_id,
                        )
                        if not marked:
                            raise HTTPException(
                                status_code=500,
                                detail=(
                                    f"Подтверждение для DRAFT #{task_id} доставлено, но сервер "
                                    "не смог сохранить delivery marker; не отправляйте повторно"
                                ),
                            ) from marker_error
                    else:
                        current = await get_duplicate_task(engine, task_id)
                        if current is not None and duplicate_draft_notification_delivered(current):
                            await _consume_preview_best_effort(
                                redis,
                                preview_token=body.preview_token,
                                stored=stored,
                                task_id=task_id,
                            )
                        else:
                            try:
                                owns_lock = not lock_lost.is_set() and await delivery_lock_owned(
                                    redis,
                                    key=lock_key,
                                    owner_token=owner_token,
                                )
                            except Exception:
                                logger.exception(
                                    "adset duplicate: ownership delivery lock не подтверждён"
                                )
                                owns_lock = False
                            if created and owns_lock:
                                cancelled = await cancel_draft_task(
                                    engine,
                                    task_id=task_id,
                                    reason=(
                                        "owner notification raised before delivery"
                                        if delivery_error is not None
                                        else "owner notification delivery failed"
                                    ),
                                    admin_override=True,
                                )
                                if not cancelled:
                                    logger.critical(
                                        "Недоставленный новый adset duplicate draft %s "
                                        "НЕ удалось отменить",
                                        task_id,
                                    )
                                    raise HTTPException(
                                        status_code=500,
                                        detail=(
                                            f"Подтверждение не доставлено, а новый DRAFT #{task_id} "
                                            "не удалось безопасно отменить"
                                        ),
                                    )
                                raise HTTPException(
                                    status_code=503,
                                    detail=(
                                        "Черновик отменён: не удалось доставить подтверждение "
                                        "владельцу"
                                    ),
                                ) from delivery_error
                            raise HTTPException(
                                status_code=503,
                                detail=(
                                    f"Подтверждение для существующего DRAFT #{task_id} не "
                                    "доставлено; задача сохранена для безопасного повтора"
                                ),
                            ) from delivery_error
            finally:
                renewal_task.cancel()
                with suppress(asyncio.CancelledError):
                    await renewal_task
                try:
                    await release_delivery_lock(
                        redis,
                        key=lock_key,
                        owner_token=owner_token,
                    )
                except Exception:
                    logger.exception("adset duplicate: delivery lock не удалось безопасно снять")

    if stored.consumed_task_id is not None:
        task_id = stored.consumed_task_id

    task = await get_duplicate_task(engine, task_id)
    if task is None:
        raise HTTPException(status_code=500, detail="Созданный DRAFT не найден")
    return AdsetDuplicateDraftOut(
        task_id=task.id,
        status=task.status,
        expires_at=task.created_at + timedelta(seconds=DRAFT_TTL_SECONDS),
    )


@router.get("/{task_id}", response_model=AdsetDuplicateStatusOut)
async def get_adset_duplicate_status(
    engine: DepEngine,
    task_id: int = Path(..., ge=1),
) -> AdsetDuplicateStatusOut:
    """Статус только duplicate_adset_structure; чужие task IDs возвращают 404."""
    task = await get_duplicate_task(engine, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача дублирования не найдена")
    return AdsetDuplicateStatusOut.model_validate(serialize_duplicate_task(task))


__all__ = ["router"]
