# -*- coding: utf-8 -*-
"""Outbox-обёртка над core/tasks/queue.py для task_type='meta_api_mutation'.

Контракты:
- create_mutation_task: статус 'pending' (executed сразу worker'ом)
- create_draft_task: статус 'draft' (ждёт подтверждения в TG/TMA)
- approve_draft: 'draft' → 'pending'
- cancel_draft_task: 'draft' → 'cancelled' с creator/owner ACL
- cancel_task: любой не-финальный → 'cancelled'
- claim_pending → core.tasks.queue.claim_next_task с фильтром по task_type
- mark_*  → проксируем в core.tasks.queue
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import (
    Task,
    TaskClaim,
    checkpoint_duplicate_adset_structure,
    claim_next_task,
    create_task,
    mark_failed,
    mark_succeeded,
    requeue_duplicate_recovery,
    requeue_for_retry,
)

logger = logging.getLogger(__name__)

_TASK_TYPE = "meta_api_mutation"


def default_idempotency_key(
    payload: MetaMutationPayload,
    *,
    requested_by: str,
    salt: str | None = None,
) -> str:
    """Сгенерировать стабильный idempotency_key для mutation.

    Формат: 'meta:{mutation_kind}:{target_id}:{hash}'
    Хеш берёт mutation_kind+target_id+params+requested_by+salt — повторный вызов
    с теми же параметрами вернёт тот же ключ (UNIQUE conflict → no-op).

    salt используется для draft'ов: каждый draft уникален, salt=ISO-timestamp.
    """
    payload_str = json.dumps(
        {
            "kind": payload.mutation_kind,
            "target": payload.target_id,
            "params": payload.params,
            "by": requested_by,
            "salt": salt or "",
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()[:16]
    key = f"meta:{payload.mutation_kind}:{payload.target_id}:{digest}"
    return key[:128]  # match VARCHAR(128) ограничение в БД


# ====================== create ======================


async def create_mutation_task(
    engine: AsyncEngine,
    *,
    payload: MetaMutationPayload,
    requested_by: str,
    status: str = "pending",
    idempotency_key: str | None = None,
    max_attempts: int = 5,
    created_by_chat_id: int | None = None,
) -> int | None:
    """Создать meta_api_mutation задачу. None если дубликат по idempotency_key.

    created_by_chat_id заполняется только для draft'ов, инициированных через TG
    (см. approve_draft_task — проверяет совпадение). Для MCP/HTTP — None.
    """
    if status not in ("draft", "pending"):
        raise ValueError(f"create_mutation_task: status='{status}' не поддерживается")
    key = idempotency_key or default_idempotency_key(payload, requested_by=requested_by)
    bulk_target_lock_keys: tuple[str, ...] = ()
    if payload.mutation_kind == "bulk_status_change":
        raw_ad_ids = payload.params.get("ad_ids") or []
        if isinstance(raw_ad_ids, list):
            bulk_target_lock_keys = tuple(
                sorted({str(ad_id).strip() for ad_id in raw_ad_ids if str(ad_id).strip()})
            )
    return await create_task(
        engine,
        task_type=_TASK_TYPE,
        idempotency_key=key,
        payload=payload.to_dict(),
        requested_by=requested_by,
        status=status,
        max_attempts=max_attempts,
        created_by_chat_id=created_by_chat_id,
        target_lock_key=(
            str(payload.target_id) if payload.mutation_kind in {"pause_ad", "activate_ad"} else None
        ),
        target_lock_keys=bulk_target_lock_keys,
    )


async def create_draft_task(
    engine: AsyncEngine,
    *,
    payload: MetaMutationPayload,
    requested_by: str,
    max_attempts: int = 3,
    created_by_chat_id: int | None = None,
) -> int | None:
    """Создать DRAFT-задачу (для AI tools).

    Каждый draft уникален: idempotency_key содержит timestamp + uuid4 salt.
    created_by_chat_id — TG chat_id инициатора (для owner ACL). Если AI работает
    через MCP — оставляем None, тогда approve через TG будет требовать админ-роль.

    MID-5 (аудит 02.07): salt = только isoformat-timestamp давал коллизию при двойном
    клике в одну секунду (два вызова в пределах одной секунды → одинаковый ISO без
    микросекунд у некоторых источников времени, а одинаковый salt → одинаковый
    idempotency_key → ON CONFLICT DO NOTHING глотал второй draft). Добавляем uuid4 —
    гарантированно уникальный компонент, столкновение невозможно даже при мгновенных
    повторных вызовах.
    """
    salt = f"{datetime.now(timezone.utc).isoformat()}:{uuid.uuid4()}"
    key = default_idempotency_key(payload, requested_by=requested_by, salt=salt)
    return await create_mutation_task(
        engine,
        payload=payload,
        requested_by=requested_by,
        status="draft",
        idempotency_key=key,
        max_attempts=max_attempts,
        created_by_chat_id=created_by_chat_id,
    )


# ====================== state transitions ======================


async def approve_draft_task(
    engine: AsyncEngine,
    *,
    task_id: int,
    approved_by: str,
    approver_chat_id: int | None = None,
    admin_override: bool = False,
) -> bool:
    """DRAFT → PENDING с owner ACL. Возвращает True если переход состоялся.

    ⚠️ MONEY-SAFETY (H-2): сам по себе chat_id-match путь (created_by_chat_id ==
    approver_chat_id) НЕ проверяет role='owner'. Поэтому ИСПОЛНЕНИЕ money-черновика
    owner-гейтится на ПЕРИМЕТРЕ вызывающих: TG-роутер (dr_ok ∈ _OWNER_ONLY_CALLBACKS)
    и TMA-эндпоинт (principal.is_owner). Новые callers ОБЯЗАНЫ owner-гейтить approve,
    иначе не-owner сможет само-подтвердить созданный им же money-черновик.

    Логика ACL:
    - Если у задачи есть created_by_chat_id, approver_chat_id обязан совпасть.
      Несовпадение → False (status остаётся 'draft'), warning в лог.
    - Если created_by_chat_id IS NULL (draft создан через MCP/HTTP без TG):
        * admin_override=True + approver_chat_id=None → разрешаем MCP-draft.
        * Иначе → False (нельзя approve безхозный draft из TG, нужен MCP-клиент).
    - admin_override=True + approver_chat_id=N → выполняется проверка
      is_admin_recipient(chat_id=N) внутри функции. Если chat_id не owner-recipient
      → PermissionError. Это защищает от callers, которые передают admin_override=True
      без предварительной проверки роли approver'а.

    approver_chat_id обязателен для approve через TG; в тестах допустим None
    только вместе с admin_override=True (MCP-draft путь).
    """
    if approver_chat_id is None and not admin_override:
        logger.warning(
            "approve_draft_task: попытка approve task_id=%s без chat_id и без admin_override",
            task_id,
        )
        return False

    # Проверка: admin_override + approver_chat_id задан → верифицируем роль внутри.
    # Защита от callers, которые выставляют admin_override=True без проверки is_admin.
    if admin_override and approver_chat_id is not None:
        if not await is_admin_recipient(engine, chat_id=approver_chat_id):
            raise PermissionError(
                f"approve_draft_task: admin_override требует role='owner', "
                f"но approver_chat_id={approver_chat_id} не является активным owner"
            )

    async with engine.begin() as conn:
        if admin_override and approver_chat_id is None:
            # MCP-draft (created_by_chat_id IS NULL) + admin_override без chat_id.
            # Используется только MCP-клиентом, не через TG.
            result = await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'pending',
                        requested_by = :rb,
                        updated_at = NOW()
                    WHERE id = :id
                      AND task_type = :tt
                      AND status = 'draft'
                      AND created_by_chat_id IS NULL
                    """
                ),
                {"id": int(task_id), "rb": approved_by[:64], "tt": _TASK_TYPE},
            )
        elif admin_override:
            # Верифицированный admin (проверка выше) подтверждает любой draft.
            result = await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'pending',
                        requested_by = :rb,
                        updated_at = NOW()
                    WHERE id = :id
                      AND task_type = :tt
                      AND status = 'draft'
                    """
                ),
                {"id": int(task_id), "rb": approved_by[:64], "tt": _TASK_TYPE},
            )
        else:
            # Обычный путь: совпадение chat_id обязательно.
            result = await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'pending',
                        requested_by = :rb,
                        updated_at = NOW()
                    WHERE id = :id
                      AND task_type = :tt
                      AND status = 'draft'
                      AND created_by_chat_id = :ccid
                    """
                ),
                {
                    "id": int(task_id),
                    "rb": approved_by[:64],
                    "tt": _TASK_TYPE,
                    "ccid": int(approver_chat_id),
                },
            )

    changed = (result.rowcount or 0) > 0
    if not changed:
        logger.warning(
            "approve_draft_task: отказ — task_id=%s, approver_chat_id=%s, admin_override=%s "
            "(чужой draft, уже не draft или missing created_by_chat_id)",
            task_id,
            approver_chat_id,
            admin_override,
        )
    return changed


async def is_admin_recipient(engine: AsyncEngine, *, chat_id: int) -> bool:
    """Возвращает True если chat_id — активный recipient с role='owner'."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT 1 FROM telegram_recipients
                    WHERE chat_id = :cid
                      AND role = 'owner'
                      AND revoked_at IS NULL
                    LIMIT 1
                    """
                ),
                {"cid": int(chat_id)},
            )
        ).first()
    return row is not None


async def cancel_task(
    engine: AsyncEngine,
    *,
    task_id: int,
    reason: str,
) -> bool:
    """Отменить задачу (любой не-финальный статус → cancelled). True если применили."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'cancelled',
                    completed_at = NOW(),
                    last_error = :err,
                    updated_at = NOW()
                WHERE id = :id
                  AND task_type = :tt
                  AND status NOT IN ('succeeded', 'failed', 'cancelled')
                """
            ),
            {
                "id": int(task_id),
                "err": (reason or "cancelled")[:8000],
                "tt": _TASK_TYPE,
            },
        )
    return (result.rowcount or 0) > 0


async def cancel_draft_task(
    engine: AsyncEngine,
    *,
    task_id: int,
    reason: str,
    canceller_chat_id: int | None = None,
    admin_override: bool = False,
) -> bool:
    """Атомарно отменить только DRAFT с creator/owner ACL.

    В отличие от :func:`cancel_task`, этот переход никогда не трогает
    ``pending``/``running``. Поздний ``dr_cancel`` после ``dr_ok`` не должен
    маскировать уже отправленную в Meta мутацию.
    """
    if canceller_chat_id is None and not admin_override:
        logger.warning(
            "cancel_draft_task: попытка cancel task_id=%s без chat_id и admin_override",
            task_id,
        )
        return False

    if admin_override and canceller_chat_id is not None:
        if not await is_admin_recipient(engine, chat_id=canceller_chat_id):
            raise PermissionError(
                "cancel_draft_task: admin_override требует role='owner', "
                f"но canceller_chat_id={canceller_chat_id} не является активным owner"
            )

    where_acl = ""
    params: dict[str, Any] = {
        "id": int(task_id),
        "err": (reason or "cancelled")[:8000],
        "tt": _TASK_TYPE,
    }
    if not admin_override:
        where_acl = "AND created_by_chat_id = :ccid"
        params["ccid"] = int(canceller_chat_id)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                f"""
                UPDATE task_queue
                SET status = 'cancelled',
                    completed_at = NOW(),
                    last_error = :err,
                    updated_at = NOW()
                WHERE id = :id
                  AND task_type = :tt
                  AND status = 'draft'
                  {where_acl}
                """
            ),
            params,
        )

    changed = (result.rowcount or 0) > 0
    if not changed:
        logger.warning(
            "cancel_draft_task: отказ — task_id=%s, canceller_chat_id=%s, "
            "admin_override=%s (чужой draft или уже не draft)",
            task_id,
            canceller_chat_id,
            admin_override,
        )
    return changed


# ====================== claim/finalize ======================


async def claim_pending_task(engine: AsyncEngine) -> TaskClaim:
    """Захват одной задачи task_type='meta_api_mutation' из очереди."""
    return await claim_next_task(engine, task_type=_TASK_TYPE)


async def mark_task_succeeded(
    engine: AsyncEngine,
    *,
    task_id: int,
    result: dict[str, Any] | None = None,
) -> bool:
    """Прокси к core.tasks.queue.mark_succeeded. См. там про bool-семантику."""
    return await mark_succeeded(engine, task_id=task_id, result=result)


async def mark_task_failed(
    engine: AsyncEngine,
    *,
    task_id: int,
    error: str,
    result: dict[str, Any] | None = None,
) -> bool:
    """Прокси к core.tasks.queue.mark_failed. См. там про bool-семантику."""
    return await mark_failed(engine, task_id=task_id, error=error, result=result)


async def checkpoint_duplicate_progress(
    engine: AsyncEngine,
    *,
    task_id: int,
    checkpoint: dict[str, Any],
) -> bool:
    """Persist progress only for a running duplicate_adset_structure task."""
    return await checkpoint_duplicate_adset_structure(
        engine,
        task_id=task_id,
        checkpoint=checkpoint,
    )


async def defer_duplicate_recovery(
    engine: AsyncEngine,
    *,
    task_id: int,
    checkpoint: dict[str, Any],
    error: str,
    delay_seconds: int = 60,
) -> bool:
    """Requeue PAUSE-only crash recovery independently of create retry limits."""
    return await requeue_duplicate_recovery(
        engine,
        task_id=task_id,
        checkpoint=checkpoint,
        error=error,
        delay_seconds=delay_seconds,
    )


async def requeue_task(
    engine: AsyncEngine,
    *,
    task: Task,
    error: str,
) -> bool:
    """Решить retry vs final fail и обновить запись.

    True — retry поставлен, False — финальный fail.
    """
    return await requeue_for_retry(
        engine,
        task_id=task.id,
        error=error,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
    )


# ====================== inspect ======================


@dataclass(slots=True, frozen=True)
class DraftView:
    """Снимок DRAFT-задачи для UI (TG/TMA Drafts page)."""

    id: int
    payload: MetaMutationPayload
    requested_by: str
    created_at: datetime | None


async def list_drafts(engine: AsyncEngine, *, limit: int = 50) -> list[DraftView]:
    """Список DRAFT meta-mutation задач (для TG /drafts и TMA)."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, payload, requested_by, created_at
                    FROM task_queue
                    WHERE task_type = :tt AND status = 'draft'
                    ORDER BY created_at DESC
                    LIMIT :lim
                    """
                ),
                {"tt": _TASK_TYPE, "lim": int(limit)},
            )
        ).all()

    out: list[DraftView] = []
    for row in rows:
        raw_payload = row[1]
        if isinstance(raw_payload, str):
            raw_payload = json.loads(raw_payload)
        try:
            payload = MetaMutationPayload.from_dict(raw_payload or {})
        except (KeyError, ValueError):
            logger.warning("Кривой payload в draft task id=%s, пропускаю", row[0])
            continue
        out.append(
            DraftView(
                id=int(row[0]),
                payload=payload,
                requested_by=str(row[2] or ""),
                created_at=row[3],
            )
        )
    return out
