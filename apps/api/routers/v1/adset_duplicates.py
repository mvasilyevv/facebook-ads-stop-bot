# -*- coding: utf-8 -*-
"""Preview + explicit web launch for duplicating selected ads from one source adset."""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Path

from apps.api.deps import DepEngine, DepRedis
from apps.api.routers.v1.schemas.adset_duplicates import (
    AdsetDuplicateLaunchIn,
    AdsetDuplicateLaunchOut,
    AdsetDuplicatePreviewIn,
    AdsetDuplicatePreviewOut,
    AdsetDuplicateStatusOut,
)
from core.adset_duplicates.service import (
    AccountMetadata,
    AdsetDuplicateError,
    DuplicateSource,
    StoredDuplicatePreview,
    build_duplicate_preview,
    create_duplicate_draft,
    fetch_account_metadata,
    get_duplicate_task,
    load_duplicate_source,
    load_stored_preview,
    mark_preview_consumed,
    resolve_duplicate_source_hierarchy,
    save_stored_preview,
    serialize_duplicate_task,
)
from core.meta_api.audit import AuditedMetaApiClient
from core.meta_api.ownership import check_ad_ownership, load_owner_tag
from core.meta_api.queue import approve_draft_task
from core.tasks.queue import DRAFT_TTL_SECONDS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools/adset-duplicates", tags=["tools"])


def _http_error(exc: AdsetDuplicateError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


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


async def _load_meta_context(
    engine: object,
    source: DuplicateSource,
) -> tuple[DuplicateSource, AccountMetadata]:
    """Hydrate missing local IDs and load account metadata through read-only Graph GETs."""
    client = AuditedMetaApiClient(
        engine=engine,
        initiated_by="api_adset_duplicate_preview",
        host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
    )
    try:
        await client.start()
        resolved_source = await resolve_duplicate_source_hierarchy(client, source)
        account = await fetch_account_metadata(client, resolved_source.account_id)
        return resolved_source, account
    except AdsetDuplicateError:
        raise
    except Exception as exc:
        logger.warning("adset duplicate: Meta hierarchy/metadata недоступны", exc_info=True)
        raise AdsetDuplicateError(
            "Не удалось получить hierarchy/currency/timezone рекламного кабинета",
            status_code=503,
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
        source, account = await _load_meta_context(engine, source)
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


@router.post("/launch", response_model=AdsetDuplicateLaunchOut)
async def launch_adset_duplicate(
    body: AdsetDuplicateLaunchIn,
    engine: DepEngine,
    redis: DepRedis,
) -> AdsetDuplicateLaunchOut:
    """Creates an idempotent task and launches it after explicit web confirmation."""
    task_id: int | None = None
    stored: StoredDuplicatePreview | None = None
    try:
        stored = await load_stored_preview(redis, body.preview_token)
        if stored.consumed_task_id is not None:
            task_id = stored.consumed_task_id
        else:
            task_id, _created = await create_duplicate_draft(engine, stored=stored)
    except AdsetDuplicateError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        logger.exception("adset duplicate task creation failed")
        raise HTTPException(status_code=503, detail="Не удалось создать задачу") from exc

    assert task_id is not None and stored is not None
    task = await get_duplicate_task(engine, task_id)
    if task is None:
        raise HTTPException(status_code=500, detail="Созданная задача не найдена")

    if task.status == "draft":
        try:
            approved = await approve_draft_task(
                engine,
                task_id=task_id,
                approved_by="web:adset_duplicate",
                admin_override=True,
            )
        except Exception as exc:
            logger.exception("adset duplicate: web approval failed for task=%s", task_id)
            raise HTTPException(
                status_code=503,
                detail="Не удалось запустить задачу; безопасный черновик сохранён",
            ) from exc
        if not approved:
            task = await get_duplicate_task(engine, task_id)
            if task is None or task.status == "draft":
                raise HTTPException(status_code=409, detail="Не удалось подтвердить запуск задачи")
    elif task.status in {"cancelled", "failed", "expired"}:
        raise HTTPException(
            status_code=409,
            detail="Этот запуск уже завершён; сформируйте новый preview",
        )

    await _consume_preview_best_effort(
        redis,
        preview_token=body.preview_token,
        stored=stored,
        task_id=task_id,
    )

    task = await get_duplicate_task(engine, task_id)
    if task is None:
        raise HTTPException(status_code=500, detail="Созданная задача не найдена")
    return AdsetDuplicateLaunchOut(
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
