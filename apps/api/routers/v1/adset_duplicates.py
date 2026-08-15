# -*- coding: utf-8 -*-
"""Preview + explicit web launch for duplicating selected ads from one source adset."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Path, Request

from apps.api.deps import DepEngine
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
    build_duplicate_preview,
    create_duplicate_task,
    fetch_account_metadata,
    get_duplicate_task,
    load_duplicate_source,
    resolve_duplicate_source_hierarchy,
    save_stored_preview,
    serialize_duplicate_task,
)
from core.meta_api.audit import AuditedMetaApiClient
from core.meta_api.ownership import check_ad_ownership, load_owner_tag
from core.safe_diagnostics import safe_exception_diagnostic
from core.tasks.browser_fence import (
    BrowserFenceLeaseLost,
    BrowserOperationBlocked,
    BrowserOperationFence,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools/adset-duplicates", tags=["tools"])


def _http_error(exc: AdsetDuplicateError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _request_principal(request: Request) -> str:
    value = getattr(request.state, "operator_principal", None)
    if not isinstance(value, str) or not value.strip():
        raise AdsetDuplicateError(
            "Подтверждённый operator principal отсутствует",
            status_code=403,
        )
    return value.strip()


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
        async with BrowserOperationFence(
            engine,
            operation_kind="adset_duplicate_preview",
            target=source.account_id,
        ) as fence:
            await client.start()
            resolved_source = await resolve_duplicate_source_hierarchy(client, source)
            account = await fetch_account_metadata(client, resolved_source.account_id)
            await fence.assert_held()
            return resolved_source, account
    except BrowserOperationBlocked as exc:
        raise AdsetDuplicateError(
            "Vision maintenance is active; preview was not started",
            status_code=409,
        ) from exc
    except BrowserFenceLeaseLost as exc:
        raise AdsetDuplicateError(
            "Preview fence was lost; retry after reconciliation",
            status_code=503,
        ) from exc
    except AdsetDuplicateError:
        raise
    except Exception as exc:
        logger.warning(
            "adset duplicate: Meta hierarchy/metadata недоступны (%s)",
            safe_exception_diagnostic(exc),
        )
        raise AdsetDuplicateError(
            "Не удалось получить hierarchy/currency/timezone рекламного кабинета",
            status_code=503,
        ) from exc
    finally:
        await client.close()


@router.post("/preview", response_model=AdsetDuplicatePreviewOut)
async def preview_adset_duplicate(
    body: AdsetDuplicatePreviewIn,
    request: Request,
    engine: DepEngine,
) -> AdsetDuplicatePreviewOut:
    """Read-only dry-run; сохраняет канонический план в PostgreSQL на 15 минут."""
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
                "Исходное объявление вне доступного owner-scope",
                status_code=403,
            )
        source, account = await _load_meta_context(engine, source)
        preview, task_params = build_duplicate_preview(
            source=source,
            account=account,
            campaign_count=body.campaign_count,
            adsets_per_campaign=body.adsets_per_campaign,
            budget_level=body.budget_level,
            daily_budget=body.daily_budget,
            requested_start_date=body.start_date,
            campaign_name_base=body.campaign_name_base,
            adset_name_base=body.adset_name_base,
            owner_tag=owner_tag,
        )
        public_preview = await save_stored_preview(
            engine,
            preview=preview,
            task_params=task_params,
            idempotency_token=body.idempotency_token,
            principal=_request_principal(request),
        )
    except AdsetDuplicateError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        logger.error(
            "adset duplicate preview failed (%s)",
            safe_exception_diagnostic(exc),
        )
        raise HTTPException(status_code=503, detail="Не удалось сохранить preview") from exc
    return AdsetDuplicatePreviewOut.model_validate(public_preview)


@router.post("/launch", response_model=AdsetDuplicateLaunchOut)
async def launch_adset_duplicate(
    body: AdsetDuplicateLaunchIn,
    request: Request,
    engine: DepEngine,
) -> AdsetDuplicateLaunchOut:
    """Queues one idempotent durable task after the explicit web launch request."""
    try:
        principal = _request_principal(request)
        task_id, _created = await create_duplicate_task(
            engine,
            preview_token=body.preview_token,
            principal=principal,
        )
    except AdsetDuplicateError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        logger.error(
            "adset duplicate task creation failed (%s)",
            safe_exception_diagnostic(exc),
        )
        raise HTTPException(status_code=503, detail="Не удалось создать задачу") from exc

    task = await get_duplicate_task(engine, task_id, principal=principal)
    if task is None:
        raise HTTPException(status_code=500, detail="Созданная задача не найдена")

    return AdsetDuplicateLaunchOut(
        task_id=task.id,
        status=task.status,
    )


@router.get("/{task_id}", response_model=AdsetDuplicateStatusOut)
async def get_adset_duplicate_status(
    request: Request,
    engine: DepEngine,
    task_id: int = Path(..., ge=1),
) -> AdsetDuplicateStatusOut:
    """Статус только duplicate_adset_structure; чужие task IDs возвращают 404."""
    try:
        task = await get_duplicate_task(
            engine,
            task_id,
            principal=_request_principal(request),
        )
    except AdsetDuplicateError as exc:
        raise _http_error(exc) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Задача дублирования не найдена")
    return AdsetDuplicateStatusOut.model_validate(serialize_duplicate_task(task))


__all__ = ["router"]
