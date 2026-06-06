# -*- coding: utf-8 -*-
"""Admin-роутер DRAFT meta-mutation задач для основного дашборда (X-API-Key зона).

Telegram Mini App использует /tma/draft-tasks с per-user ACL по chat_id.
Десктоп-дашборд — доверенная admin-зона без Telegram-личности, поэтому:
- list/reject — без per-user ограничений (доверенная зона);
- confirm подтверждает только «безхозные» черновики (created_by_chat_id IS NULL,
  созданные через MCP/HTTP) через готовый admin_override-путь approve_draft_task.
  Черновики, созданные конкретным TG-пользователем, подтверждаются в Telegram —
  здесь confirm вернёт 409 (ACL-ядро не трогаем, переиспользуем как есть).

Endpoints (prefix /api добавляется в register_all):
    GET  /dashboard/draft-tasks               — список DRAFT meta_api_mutation
    POST /dashboard/draft-tasks/{id}/confirm  — DRAFT → PENDING (admin_override)
    POST /dashboard/draft-tasks/{id}/reject   — DRAFT → CANCELLED
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.tma import (
    TmaDraftActionResponse,
    TmaDraftOut,
    TmaRejectRequest,
)
from core.meta_api.queue import approve_draft_task, cancel_task, list_drafts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/draft-tasks", tags=["drafts"])


@router.get("", response_model=list[TmaDraftOut])
async def list_draft_tasks(
    engine: DepEngine,
    kind: str | None = Query(default=None, description="Фильтр по mutation_kind"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[TmaDraftOut]:
    """Список DRAFT meta-mutation задач (status='draft', task_type='meta_api_mutation')."""
    drafts = await list_drafts(engine, limit=limit)
    out: list[TmaDraftOut] = []
    for d in drafts:
        if kind and d.payload.mutation_kind != kind:
            continue
        # expires_at вычисляется из created_at + DRAFT_TTL_SECONDS.
        # current_state = None в list-endpoint'е (дорого делать JOIN для N строк).
        out.append(
            TmaDraftOut.from_created_at(
                id=d.id,
                mutation_kind=d.payload.mutation_kind,
                target_id=d.payload.target_id,
                ad_account_id=d.payload.ad_account_id,
                payload=dict(d.payload.params or {}),
                requested_by=d.requested_by,
                created_at_iso=d.created_at.isoformat() if d.created_at else None,
            )
        )
    return out


@router.post("/{task_id}/confirm", response_model=TmaDraftActionResponse)
async def confirm_draft_task(task_id: int, engine: DepEngine) -> TmaDraftActionResponse:
    """DRAFT → PENDING.

    Admin-зона подтверждает только безхозные черновики (created_by_chat_id IS NULL,
    созданные через MCP/HTTP). Черновики от конкретного TG-пользователя — 409
    (их подтверждают в Telegram). Money-критично: ACL внутри approve_draft_task.
    """
    try:
        ok = await approve_draft_task(
            engine,
            task_id=task_id,
            approved_by="dashboard",
            approver_chat_id=None,
            admin_override=True,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Нельзя подтвердить с дашборда: уже не draft или черновик создан в "
            "Telegram (подтвердите его в Telegram).",
        )
    logger.info("Dashboard draft confirm: id=%s", task_id)
    return TmaDraftActionResponse(ok=True, detail="Задача подтверждена и поставлена в очередь")


@router.post("/{task_id}/reject", response_model=TmaDraftActionResponse)
async def reject_draft_task(
    task_id: int,
    body: TmaRejectRequest,
    engine: DepEngine,
) -> TmaDraftActionResponse:
    """Отклонить (cancel) DRAFT-задачу."""
    reason = body.reason or "rejected via dashboard"
    ok = await cancel_task(engine, task_id=task_id, reason=reason)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Нельзя отклонить: задача уже финализирована.",
        )
    logger.info("Dashboard draft reject: id=%s", task_id)
    return TmaDraftActionResponse(ok=True, detail="Черновик отклонён")
