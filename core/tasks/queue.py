# -*- coding: utf-8 -*-
"""Unified task_queue helpers — async API для всех outbox-воркеров.

Контракты:
- claim_next_task: FOR UPDATE SKIP LOCKED → атомарный захват + status='running'
- mark_succeeded/mark_failed: только из workspace того воркера который захватил
- requeue_for_retry: backoff = min(30 * 2^attempt, 300) сек
- create_task: INSERT ON CONFLICT (idempotency_key) DO NOTHING
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.tasks.wakeup import TASK_QUEUE_NOTIFY_CHANNEL
from core.worker_metrics import (
    TASK_CLAIM_LATENCY,
    TASK_OLDEST_PENDING_AGE,
    TASK_QUEUE_DEPTH,
)

logger = logging.getLogger(__name__)

# Допустимые значения task_type — должны совпадать с CHECK constraint в БД
TASK_TYPES = frozenset(
    {
        "meta_api_mutation",
        "observer_scan",
        "campaign_create",
        "tracker_event_process",
    }
)

# Допустимые статусы — должны совпадать с CHECK constraint
TASK_STATUSES = frozenset({"pending", "running", "succeeded", "failed", "retrying", "cancelled"})

TASK_LANES = frozenset({"money", "interactive", "bulk", "background"})
BROWSER_BACKED_TASK_TYPES = frozenset({"meta_api_mutation", "observer_scan", "campaign_create"})
BROWSER_READY_CLAIM_TASK_TYPES = frozenset({"meta_api_mutation", "campaign_create"})

_LANE_DEFAULT_DEADLINE_SECONDS = {
    "money": 30,
    "interactive": 120,
    "bulk": 30 * 60,
    "background": 120,
}
_LANE_DEFAULT_PRIORITY = {
    "money": 100,
    "interactive": 50,
    "bulk": 20,
    "background": 0,
}
_DEFAULT_WORKER_ID = uuid.uuid4()

# Retry backoff: 30s, 60s, 120s, 240s, 300s (cap)
_RETRY_BASE_SECONDS = 30
_RETRY_MAX_SECONDS = 300


def _optional_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _has_valid_fence(lease_owner: uuid.UUID | None, lease_token: int | None) -> bool:
    """Return whether a task mutation is bound to a real claimed lease.

    All state changes after claim are fail-closed.  Apart from being unsafe, the
    old nullable predicate was not executable by asyncpg because PostgreSQL
    could not infer the type of ``:lease_owner IS NULL``.
    """
    return lease_owner is not None and lease_token is not None and int(lease_token) > 0


@dataclass(kw_only=True)
class Task:
    """Снимок строки task_queue для воркера."""

    id: int
    task_type: str
    status: str
    idempotency_key: str
    payload: dict[str, Any]
    attempt_count: int
    max_attempts: int
    requested_by: str
    last_error: str | None
    created_at: datetime
    external_started_at: datetime | None
    result: dict[str, Any] | None
    lane: str
    priority: int
    available_at: datetime
    deadline_at: datetime | None
    lease_owner: uuid.UUID | None
    lease_token: int
    lease_expires_at: datetime | None
    cancel_requested_at: datetime | None
    cancel_reason: str | None
    correlation_id: uuid.UUID
    browser_profile_id: str | None = None
    browser_readiness_generation: int | None = None


@dataclass
class TaskClaim:
    """Результат claim_next_task — задача либо есть, либо нет."""

    task: Task | None = None
    queue_empty: bool = True
    browser_profile_id: str | None = None
    browser_readiness_generation: int | None = None


def _calc_retry_available_at(attempt: int) -> datetime:
    """Exponential backoff: 30s, 60s, 120s, 240s, 300s+."""
    delay = min(_RETRY_BASE_SECONDS * (2**attempt), _RETRY_MAX_SECONDS)
    return datetime.now(timezone.utc) + timedelta(seconds=delay)


def _row_to_task(row: Any) -> Task:
    """Конвертер sqlalchemy row → Task dataclass."""
    mapping = getattr(row, "_mapping", None)
    if mapping is None:
        raise TypeError("task query must return a SQLAlchemy Row with named columns")
    payload = mapping["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    raw_result = mapping["result"]
    if isinstance(raw_result, str):
        raw_result = json.loads(raw_result)
    return Task(
        id=int(mapping["id"]),
        task_type=str(mapping["task_type"]),
        status=str(mapping["status"]),
        idempotency_key=str(mapping["idempotency_key"]),
        payload=payload or {},
        attempt_count=int(mapping["attempt_count"] or 0),
        max_attempts=int(mapping["max_attempts"] or 5),
        requested_by=str(mapping["requested_by"] or ""),
        last_error=mapping["last_error"],
        created_at=mapping["created_at"],
        external_started_at=mapping["external_started_at"],
        result=raw_result if isinstance(raw_result, dict) else None,
        lane=str(mapping["lane"]),
        priority=int(mapping["priority"]),
        available_at=mapping["available_at"],
        deadline_at=mapping["deadline_at"],
        lease_owner=mapping["lease_owner"],
        lease_token=int(mapping["lease_token"]),
        lease_expires_at=mapping["lease_expires_at"],
        cancel_requested_at=mapping["cancel_requested_at"],
        cancel_reason=mapping["cancel_reason"],
        correlation_id=mapping["correlation_id"],
    )


def infer_task_lane(
    task_type: str,
    payload: dict[str, Any],
    *,
    requested_by: str = "",
) -> str:
    """Choose the durable scheduler lane from business semantics."""
    mutation_kind = str(payload.get("mutation_kind") or "")
    requested_action = str(payload.get("action") or "")
    if (
        task_type == "meta_api_mutation"
        and mutation_kind == "pause_ad"
        and requested_by == "bot_auto_stop"
    ):
        return "money"
    if task_type == "campaign_create" or (
        task_type == "meta_api_mutation"
        and mutation_kind
        in {
            "bulk_status_change",
            "duplicate_adset_structure",
        }
    ):
        return "bulk"
    if task_type == "tracker_event_process":
        return "background"
    if task_type == "observer_scan":
        return "interactive"
    if requested_action:
        return "interactive"
    return "interactive"


# ====================== create ======================


async def create_task(
    engine: AsyncEngine,
    *,
    task_type: str,
    idempotency_key: str,
    payload: dict[str, Any],
    requested_by: str,
    status: str = "pending",
    max_attempts: int = 5,
    created_by_chat_id: int | None = None,
    target_lock_key: str | None = None,
    target_lock_keys: Sequence[str] | None = None,
    lane: str | None = None,
    priority: int | None = None,
    available_at: datetime | None = None,
    deadline_at: datetime | None = None,
    correlation_id: uuid.UUID | None = None,
    connection: AsyncConnection | None = None,
) -> int | None:
    """INSERT new task. Idempotent: если idempotency_key уже есть — None.

    Tasks are created pending and become runnable immediately.
    """
    if task_type not in TASK_TYPES:
        raise ValueError(f"Unknown task_type: {task_type}")
    if status not in TASK_STATUSES:
        raise ValueError(f"Unknown status: {status}")
    effective_lane = lane or infer_task_lane(
        task_type,
        payload,
        requested_by=requested_by,
    )
    if effective_lane not in TASK_LANES:
        raise ValueError(f"Unknown task lane: {effective_lane}")
    now = datetime.now(timezone.utc)
    effective_available_at = available_at or now
    effective_deadline_at = deadline_at
    if effective_deadline_at is None and effective_lane != "money":
        effective_deadline_at = now + timedelta(
            seconds=_LANE_DEFAULT_DEADLINE_SECONDS[effective_lane]
        )
    effective_priority = (
        _LANE_DEFAULT_PRIORITY[effective_lane] if priority is None else int(priority)
    )

    async def _insert(conn: AsyncConnection) -> int | None:
        lock_keys = [target_lock_key] if target_lock_key is not None else []
        lock_keys.extend(target_lock_keys or ())
        if any(not key for key in lock_keys):
            raise ValueError("target lock keys must not be empty")
        # Deterministic order prevents deadlocks when overlapping bulk tasks lock
        # more than one ad in concurrent transactions.
        for lock_key in sorted(set(lock_keys)):
            # Один и тот же per-target mutex используют pause/activate writers,
            # recommendation confirmation и external-call boundary. Так проверка
            # отсутствия pause_ad и INSERT activate_ad остаются атомарными
            # относительно конкурентного создания новой pause_ad.
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": lock_key},
            )
        result = await conn.execute(
            text(
                """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload,
                     attempt_count, max_attempts, requested_by, created_by_chat_id,
                     lane, priority, available_at, deadline_at, correlation_id)
                VALUES
                    (:tt, :st, :ik, CAST(:pl AS JSONB), 0, :ma, :rb, :ccid,
                     :lane, :priority, :available_at, :deadline_at, :correlation_id)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "tt": task_type,
                "st": status,
                "ik": idempotency_key,
                "pl": json.dumps(payload),
                "ma": int(max_attempts),
                "rb": requested_by,
                "ccid": int(created_by_chat_id) if created_by_chat_id is not None else None,
                "lane": effective_lane,
                "priority": effective_priority,
                "available_at": effective_available_at,
                "deadline_at": effective_deadline_at,
                "correlation_id": correlation_id or uuid.uuid4(),
            },
        )
        row = result.first()
        if row is None:
            return None
        task_id = int(row[0])
        await conn.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {
                "channel": TASK_QUEUE_NOTIFY_CHANNEL,
                "payload": json.dumps(
                    {
                        "task_id": task_id,
                        "task_type": task_type,
                        "lane": effective_lane,
                    },
                    separators=(",", ":"),
                ),
            },
        )
        return task_id

    if connection is not None:
        return await _insert(connection)
    async with engine.begin() as conn:
        return await _insert(conn)


# ====================== claim ======================


async def _transition_correlated_incident(
    conn: AsyncConnection,
    *,
    task_id: int,
    correlation_id: uuid.UUID | None,
    phase: Literal["executing", "confirmed", "failed", "cancelled", "unknown", "recovered"],
    payload: dict[str, Any] | None,
) -> bool:
    """Advance an incident and enqueue its card edit in the task transaction.

    Only commands explicitly correlated to an active incident are affected.
    This keeps task/FSM callers generic while eliminating the crash window
    between a money-task lifecycle transition and its operator notification.
    """
    if correlation_id is None:
        return False

    if phase == "executing":
        incident_status = "executing"
        event_type = "action_executing"
        severity = "warning"
        status_label = "Выполняется"
        summary = "Денежное действие принято выделенным воркером."
    elif phase == "confirmed":
        incident_status = "resolved"
        event_type = "action_confirmed"
        severity = "ok"
        status_label = "Подтверждено"
        summary = "Изменение подтверждено фактическим ответом Meta."
    elif phase == "recovered":
        incident_status = "resolved"
        event_type = "incident_recovered"
        severity = "ok"
        status_label = "Угроза снята"
        summary = "Условие риска исчезло до внешнего действия; команда отменена."
    elif phase == "unknown":
        incident_status = "failed"
        event_type = "action_unknown"
        severity = "critical"
        status_label = "Результат неизвестен"
        summary = "Автоповтор запрещён: проверьте фактический статус в Meta."
    elif phase == "cancelled":
        incident_status = "failed"
        event_type = "action_cancelled"
        severity = "warning"
        status_label = "Отменено"
        summary = "Действие отменено до внешнего вызова."
    else:
        incident_status = "failed"
        event_type = "action_failed"
        severity = "critical"
        status_label = "Не выполнено"
        summary = "Действие не подтверждено; откройте карточку для диагностики."

    timestamp_column = "resolved_at = NOW()," if incident_status in {"resolved", "failed"} else ""
    incident = (
        await conn.execute(
            text(
                f"""
                UPDATE incidents
                SET status = :status,
                    {timestamp_column}
                    summary = :summary,
                    updated_at = NOW()
                WHERE correlation_id = :correlation_id
                  AND status IN ('open','acknowledged','executing')
                RETURNING id, title, correlation_id
                """
            ),
            {
                "status": incident_status,
                "summary": summary,
                "correlation_id": correlation_id,
            },
        )
    ).first()
    if incident is None:
        return False

    # Lazy imports keep the queue layer free of a module cycle at import time.
    from core.telegram.notifications import enqueue_notification_in_transaction
    from core.telegram.schemas import NotificationCardFacts, NotificationEventSpec

    action_kind = str((payload or {}).get("mutation_kind") or "action")
    await enqueue_notification_in_transaction(
        conn,
        NotificationEventSpec(
            event_type=event_type,
            severity=severity,
            audience="owners",
            facts=NotificationCardFacts(
                title=str(incident.title),
                summary=summary,
                lines=[f"Задача #{task_id} · {action_kind}"],
                status=status_label,
            ),
            dedupe_key=f"task:{task_id}:{phase}",
            incident_id=uuid.UUID(str(incident.id)),
            correlation_id=uuid.UUID(str(incident.correlation_id)),
        ),
    )
    return True


def _is_money_stop_payload(payload: dict[str, Any] | None) -> bool:
    """Return whether failure can leave ads spending after a stop command."""
    values = payload or {}
    mutation_kind = str(values.get("mutation_kind") or "")
    if mutation_kind == "pause_ad":
        return True
    if mutation_kind != "bulk_status_change":
        return False
    params = values.get("params") if isinstance(values.get("params"), dict) else {}
    requested_status = str(params.get("action") or "").upper()
    return requested_status in {"PAUSE", "PAUSED"}


def is_money_changing_task(
    *,
    task_type: str,
    payload: dict[str, Any] | None,
) -> bool:
    """Classify monetary effects independently from scheduler routing.

    ``lane`` answers which worker may claim a task; it is not business
    authority and cannot suppress failure notifications.  Only explicit Meta
    status mutations belong here.  Scans, reads and irreversible duplicate
    flows keep their dedicated incident projections.
    """
    if task_type != "meta_api_mutation":
        return False
    values = payload or {}
    mutation_kind = str(values.get("mutation_kind") or "")
    if mutation_kind in {"pause_ad", "activate_ad"}:
        return True
    if mutation_kind != "bulk_status_change":
        return False
    params = values.get("params") if isinstance(values.get("params"), dict) else {}
    requested_status = str(params.get("action") or "").upper()
    return requested_status in {"PAUSE", "PAUSED", "ACTIVATE", "ACTIVE"}


def _is_partial_money_result(
    payload: dict[str, Any] | None,
    result: dict[str, Any] | None,
) -> bool:
    if str((payload or {}).get("mutation_kind") or "") != "bulk_status_change":
        return False
    try:
        return (
            int((result or {}).get("succeeded") or 0) > 0
            and int((result or {}).get("failed") or 0) > 0
        )
    except (TypeError, ValueError):
        return False


async def _enqueue_standalone_money_failure(
    conn: AsyncConnection,
    *,
    task_id: int,
    correlation_id: uuid.UUID | None,
    phase: Literal["failed", "unknown"],
    payload: dict[str, Any],
    requested_by: str,
    lane: str,
    task_type: str,
    dedupe_suffix: str | None = None,
) -> None:
    """Persist an uncorrelated critical money-action card in the task transaction."""
    if not is_money_changing_task(task_type=task_type, payload=payload):
        return

    from core.telegram.notifications import enqueue_notification_in_transaction
    from core.telegram.schemas import NotificationCardFacts, NotificationEventSpec

    mutation_kind = str(
        payload.get("mutation_kind") or payload.get("action") or task_type or "action"
    )
    target_id = str(payload.get("target_id") or "unknown")
    is_unknown = phase == "unknown"
    is_stop_action = _is_money_stop_payload(payload)
    title = (
        ("Авто-стоп не подтверждён" if requested_by == "bot_auto_stop" else "Пауза не подтверждена")
        if is_stop_action
        else "Денежное действие не подтверждено"
    )
    risk = (
        "Объявление может продолжать тратить бюджет"
        if is_stop_action
        else "Фактическое состояние Meta требует ручной проверки"
    )
    await enqueue_notification_in_transaction(
        conn,
        NotificationEventSpec(
            event_type="action_unknown" if is_unknown else "action_failed",
            severity="critical",
            audience="owners",
            facts=NotificationCardFacts(
                title=title,
                summary=f"Цель: {target_id[:160]} · операция: {mutation_kind}",
                lines=[f"Задача #{task_id} · проверь статус вручную"],
                risk=risk,
                status="Результат неизвестен" if is_unknown else "Не выполнено",
            ),
            dedupe_key=f"task:{task_id}:{dedupe_suffix or phase}",
            correlation_id=correlation_id,
        ),
    )


async def _enqueue_standalone_campaign_unknown(
    conn: AsyncConnection,
    *,
    task_id: int,
    correlation_id: uuid.UUID | None,
    phase: Literal["failed", "unknown"],
    payload: dict[str, Any],
    result: dict[str, Any],
    task_type: str,
) -> None:
    """Open the durable campaign reconciliation incident in the task transaction."""
    if task_type != "campaign_create" or phase != "unknown":
        return

    run_id = str(payload.get("run_id") or result.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("campaign_create UNKNOWN projection requires run_id")

    created = result.get("created_ids")
    created_ids = created if isinstance(created, dict) else {}
    labels = {
        "campaigns": "Кампаний",
        "adsets": "Адсетов",
        "ads": "Объявлений",
        "creatives": "Креативов",
    }
    lines: list[str] = []
    for kind, values in created_ids.items():
        count = len(values) if isinstance(values, list) else 1
        if count > 0:
            lines.append(f"{labels.get(kind, kind)}: {count}")
    if not lines:
        lines.append(f"Задача #{task_id} · ответ Meta не подтверждён")

    from core.telegram.worker_notify import notify_recurring_incident_in_transaction

    await notify_recurring_incident_in_transaction(
        conn,
        incident_key=f"campaign-create:{run_id}:unknown",
        audience="owners",
        event_type="campaign_create_reconciliation_required",
        severity="critical",
        title="Создание кампании требует сверки",
        summary=(
            "Meta могла создать только часть объектов."
            if created_ids
            else "Meta могла принять создание, но подтверждение потеряно."
        ),
        lines=lines[:5],
        risk="Не повторяйте запуск до ручной сверки в Ads Manager.",
        resource_type="campaign_run",
        resource_id=run_id,
        correlation_id=correlation_id,
    )


async def _transition_terminal_task(
    conn: AsyncConnection,
    *,
    task_id: int,
    correlation_id: uuid.UUID | None,
    phase: Literal["failed", "cancelled", "unknown"],
    payload: dict[str, Any],
    result: dict[str, Any] | None = None,
    requested_by: str = "",
    lane: str = "",
    task_type: str = "",
) -> None:
    """Atomically project a terminal task to exactly one durable operator event."""
    has_incident_event = await _transition_correlated_incident(
        conn,
        task_id=task_id,
        correlation_id=correlation_id,
        phase=phase,
        payload=payload,
    )
    if not has_incident_event and phase in {"failed", "unknown"}:
        await _enqueue_standalone_money_failure(
            conn,
            task_id=task_id,
            correlation_id=correlation_id,
            phase=phase,
            payload=payload,
            requested_by=requested_by,
            lane=lane,
            task_type=task_type,
        )
        await _enqueue_standalone_campaign_unknown(
            conn,
            task_id=task_id,
            correlation_id=correlation_id,
            phase=phase,
            payload=payload,
            result=result or {},
            task_type=task_type,
        )


async def _project_duplicate_terminal_incident(
    conn: AsyncConnection,
    *,
    task_id: int,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Project an irreversible duplicate failure in the same task transaction."""
    if (
        payload.get("mutation_kind") != "duplicate_adset_structure"
        or result.get("checkpoint_type") != "duplicate_adset_structure"
    ):
        return

    from core.meta_api.duplicate_incidents import (
        project_duplicate_incident_in_transaction,
        resolve_duplicate_incident_in_transaction,
    )

    phase = str(result.get("phase") or "")
    if phase == "recovery_paused":
        await resolve_duplicate_incident_in_transaction(
            conn,
            task_id=task_id,
            checkpoint=result,
        )
        return
    stage: Literal["partial", "recovery_invalid"]
    stage = "recovery_invalid" if phase == "recovery_checkpoint_invalid" else "partial"
    await project_duplicate_incident_in_transaction(
        conn,
        task_id=task_id,
        checkpoint=result,
        stage=stage,
    )


async def _resolve_confirmed_autostop_incidents_in_transaction(
    conn: AsyncConnection,
    *,
    payload: dict[str, Any],
    result: dict[str, Any],
    requested_by: str,
) -> None:
    """Atomically close the matching per-ad incident on CONFIRMED PAUSED.

    This projection intentionally lives in the fenced task finalizer.  A
    process crash therefore cannot commit ``task_queue.status='succeeded'``
    while leaving the corresponding per-ad risk incident open.  A successful
    ad mutation is not evidence that the shared transport is healthy, so this
    path must never resolve a channel-wide incident.
    """
    if (
        requested_by != "bot_auto_stop"
        or payload.get("mutation_kind") != "pause_ad"
        or result.get("outcome") != "CONFIRMED"
    ):
        return
    target_id = str(payload.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("confirmed auto-stop pause_ad requires target_id")

    from core.meta_api.autostop_alert import (
        UNDELIVERED_INCIDENT_KEY_PREFIX,
    )
    from core.telegram.worker_notify import resolve_recurring_incident_in_transaction

    await resolve_recurring_incident_in_transaction(
        conn,
        incident_key=f"{UNDELIVERED_INCIDENT_KEY_PREFIX}{target_id}",
        audience="owners",
        summary=f"Объявление {target_id} подтверждено OFF.",
    )


_META_TOKEN_INVALID_INCIDENT_KEY = "meta:token-invalid"


async def _project_meta_token_incident_in_transaction(
    conn: AsyncConnection,
    *,
    task_type: str,
    result: dict[str, Any],
) -> None:
    """Atomically open the Meta re-login incident for a terminal auth failure."""
    if task_type != "meta_api_mutation" or result.get("requires_meta_reauth") is not True:
        return

    from core.telegram.worker_notify import notify_recurring_incident_in_transaction

    await notify_recurring_incident_in_transaction(
        conn,
        incident_key=_META_TOKEN_INVALID_INCIDENT_KEY,
        audience="owners",
        event_type="meta_token_invalid",
        severity="critical",
        title="Marketing API требует повторного входа",
        summary="Токен не принят Meta",
        risk="Money-операции не будут исполняться",
        lines=["Войди в Facebook в Vision-профиле"],
        resource_type="meta_session",
        resource_id="marketing-api",
    )


async def _resolve_meta_token_incident_in_transaction(
    conn: AsyncConnection,
    *,
    task_type: str,
) -> None:
    """A confirmed Meta mutation proves that the shared session works again."""
    if task_type != "meta_api_mutation":
        return

    from core.telegram.worker_notify import resolve_recurring_incident_in_transaction

    await resolve_recurring_incident_in_transaction(
        conn,
        incident_key=_META_TOKEN_INVALID_INCIDENT_KEY,
        audience="owners",
        summary="Marketing API снова принимает операции.",
    )


async def transition_correlated_incident_in_transaction(
    conn: AsyncConnection,
    *,
    task_id: int,
    correlation_id: uuid.UUID | None,
    phase: Literal["executing", "confirmed", "failed", "cancelled", "unknown", "recovered"],
    payload: dict[str, Any] | None,
) -> bool:
    """Public transaction hook for compound task/domain finalizers.

    A worker that atomically finalizes its domain row together with
    ``task_queue`` must call this before committing instead of invoking the
    standalone ``mark_*`` helpers in a second transaction.
    """
    return await _transition_correlated_incident(
        conn,
        task_id=task_id,
        correlation_id=correlation_id,
        phase=phase,
        payload=payload,
    )


async def transition_terminal_task_in_transaction(
    conn: AsyncConnection,
    *,
    task_id: int,
    correlation_id: uuid.UUID | None,
    phase: Literal["failed", "cancelled", "unknown"],
    payload: dict[str, Any],
    result: dict[str, Any] | None = None,
    requested_by: str = "",
    lane: str = "",
    task_type: str = "",
) -> None:
    """Project a compound domain/task finalizer without a post-commit gap."""
    await _transition_terminal_task(
        conn,
        task_id=task_id,
        correlation_id=correlation_id,
        phase=phase,
        payload=payload,
        result=result,
        requested_by=requested_by,
        lane=lane,
        task_type=task_type,
    )


def _returned_task_rows(result: Any) -> tuple[list[Any], int]:
    """Consume the mandatory PostgreSQL ``UPDATE .. RETURNING`` result."""
    all_rows = getattr(result, "all", None)
    if not callable(all_rows):
        raise TypeError("terminal task transition requires UPDATE .. RETURNING rows")
    rows = list(all_rows())
    return rows, len(rows)


def _returned_value(row: Any, key: str) -> Any:
    mapping = getattr(row, "_mapping", None)
    if mapping is None:
        raise TypeError("task transition must return SQLAlchemy rows with named columns")
    return mapping[key]


async def _transition_returned_terminal_tasks(
    conn: AsyncConnection,
    rows: Sequence[Any],
    *,
    force_phase: Literal["failed", "cancelled", "unknown"] | None = None,
) -> None:
    """Project direct reconciler terminal writes into incidents atomically."""
    for row in rows:
        status = str(_returned_value(row, "status"))
        if status not in {"failed", "cancelled"}:
            continue
        stored_result = _returned_value(row, "result")
        if isinstance(stored_result, str):
            try:
                stored_result = json.loads(stored_result)
            except json.JSONDecodeError:
                stored_result = None
        result_payload = stored_result if isinstance(stored_result, dict) else {}
        if force_phase is not None:
            phase = force_phase
        elif status == "cancelled":
            phase = "cancelled"
        elif (
            result_payload.get("outcome") == "UNKNOWN"
            or result_payload.get("reconcile_required") is True
        ):
            phase = "unknown"
        else:
            phase = "failed"
        payload = _returned_value(row, "payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        await _transition_terminal_task(
            conn,
            task_id=int(_returned_value(row, "id")),
            correlation_id=_optional_uuid(_returned_value(row, "correlation_id")),
            phase=phase,
            payload=payload if isinstance(payload, dict) else {},
            result=result_payload,
            requested_by=str(_returned_value(row, "requested_by") or ""),
            lane=str(_returned_value(row, "lane") or ""),
            task_type=str(_returned_value(row, "task_type") or ""),
        )


_CLAIM_SQL = text(
    """
    UPDATE task_queue
    SET status = 'running',
        lease_owner = :worker_id,
        lease_token = task_queue.lease_token + 1,
        deadline_at = CASE
          WHEN task_queue.lane = 'money' THEN
            clock_timestamp() + make_interval(secs => 30)
          ELSE task_queue.deadline_at
        END,
        lease_expires_at =
          clock_timestamp() + make_interval(secs => :lease_seconds),
        updated_at = clock_timestamp()
    WHERE id = (
        SELECT id FROM task_queue
        WHERE task_type = :tt
          AND status IN ('pending', 'retrying')
          AND lane IN :lanes
          AND available_at <= clock_timestamp()
          AND (
              lane = 'money'
              OR deadline_at IS NULL
              OR deadline_at > clock_timestamp()
          )
          AND NOT (
              task_type IN ('meta_api_mutation', 'observer_scan', 'campaign_create')
              AND EXISTS (
                  SELECT 1
                  FROM system_config AS browser_gate
                  WHERE browser_gate.key = 'browser_maintenance'
                    AND (
                        browser_gate.value->>'expires_at'
                    )::timestamptz > clock_timestamp()
              )
          )
          AND (
              cancel_requested_at IS NULL
              OR COALESCE(result->>'reconcile_required', 'false') = 'true'
          )
        ORDER BY priority DESC, available_at, created_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, task_type, status, idempotency_key, payload,
              attempt_count, max_attempts, requested_by, last_error,
              created_at, external_started_at, result,
              lane, priority, available_at, deadline_at, lease_owner,
              lease_token, lease_expires_at, cancel_requested_at, cancel_reason,
              correlation_id
    """
).bindparams(bindparam("lanes", expanding=True))


_BROWSER_READY_CLAIM_SQL = text(
    """
    WITH readiness AS MATERIALIZED (
        SELECT
            config.profile_id AS browser_profile_id,
            channel.generation AS browser_readiness_generation
        FROM vision_config AS config
        JOIN browser_channel_readiness AS channel
          ON channel.channel = 'meta_api'
         AND channel.vision_config_id = config.id
         AND channel.vision_config_updated_at = config.updated_at
         AND channel.expected_profile_id = config.profile_id
        WHERE config.singleton_key = 'default'
          AND channel.state = 'ready'
          AND channel.observed_contract_version = 5
          AND channel.observed_profile_id = config.profile_id
          AND NULLIF(channel.observed_session_id, '') IS NOT NULL
          AND channel.readiness_expires_at > clock_timestamp()
          AND NOT EXISTS (
              SELECT 1
              FROM system_config AS browser_gate
              WHERE browser_gate.key = 'browser_maintenance'
                AND (browser_gate.value->>'expires_at')::timestamptz
                      > clock_timestamp()
          )
    ),
    candidate AS MATERIALIZED (
        SELECT
            task.id,
            readiness.browser_profile_id,
            readiness.browser_readiness_generation
        FROM task_queue AS task
        CROSS JOIN readiness
        WHERE task.task_type = :tt
          AND task.status IN ('pending', 'retrying')
          AND task.lane IN :lanes
          AND task.available_at <= clock_timestamp()
          AND (
              task.lane = 'money'
              OR task.deadline_at IS NULL
              OR task.deadline_at > clock_timestamp()
          )
          AND (
              task.cancel_requested_at IS NULL
              OR COALESCE(task.result->>'reconcile_required', 'false') = 'true'
          )
        ORDER BY
            task.priority DESC,
            task.available_at,
            task.created_at,
            task.id
        FOR UPDATE OF task SKIP LOCKED
        LIMIT 1
    )
    UPDATE task_queue AS task
    SET status = 'running',
        lease_owner = :worker_id,
        lease_token = task.lease_token + 1,
        deadline_at = CASE
          WHEN task.lane = 'money' THEN
            clock_timestamp() + make_interval(secs => 30)
          ELSE task.deadline_at
        END,
        lease_expires_at =
          clock_timestamp() + make_interval(secs => :lease_seconds),
        updated_at = clock_timestamp()
    FROM candidate
    WHERE task.id = candidate.id
    RETURNING
        task.id,
        task.task_type,
        task.status,
        task.idempotency_key,
        task.payload,
        task.attempt_count,
        task.max_attempts,
        task.requested_by,
        task.last_error,
        task.created_at,
        task.external_started_at,
        task.result,
        task.lane,
        task.priority,
        task.available_at,
        task.deadline_at,
        task.lease_owner,
        task.lease_token,
        task.lease_expires_at,
        task.cancel_requested_at,
        task.cancel_reason,
        task.correlation_id,
        candidate.browser_profile_id,
        candidate.browser_readiness_generation
    """
).bindparams(bindparam("lanes", expanding=True))


async def claim_next_task(
    engine: AsyncEngine,
    *,
    task_type: str,
    lanes: Sequence[str] | None = None,
    worker_id: uuid.UUID | None = None,
    lease_seconds: int = 30 * 60,
) -> TaskClaim:
    """Атомарный захват одной задачи указанного типа.

    Использует UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED) —
    стандартный безопасный паттерн для concurrent workers.
    Если очередь пуста — queue_empty=True, task=None.
    """
    if task_type not in TASK_TYPES:
        raise ValueError(f"Unknown task_type: {task_type}")
    if lanes is None:
        effective_lanes = ("interactive", "bulk", "background")
    else:
        effective_lanes = tuple(lanes)
    if not effective_lanes or any(lane not in TASK_LANES for lane in effective_lanes):
        raise ValueError("claim_next_task requires valid non-empty lanes")
    if task_type in BROWSER_READY_CLAIM_TASK_TYPES:
        # Keep the generic queue API fail-closed for legacy/third-party callers:
        # browser-controlled work can only reach the readiness-gated SQL.
        return await claim_browser_ready_task(
            engine,
            task_type=task_type,
            lanes=effective_lanes,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
    started_at = time.perf_counter()
    async with engine.begin() as conn:
        if task_type in BROWSER_BACKED_TASK_TYPES:
            # Serialize the snapshot boundary of every browser-backed claim
            # with maintenance acquisition.  A maintenance transaction takes
            # the same advisory xact lock before committing its durable gate:
            # claims that started earlier finish first; claims that wait see
            # the committed gate in the following READ COMMITTED statement.
            # Pin this explicitly: REPEATABLE READ would retain the pre-wait
            # snapshot and reopen the exact INSERT/claim race this fence closes.
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
            await conn.execute(
                text(
                    """
                    SELECT pg_advisory_xact_lock(
                      hashtext('fb-agent'),
                      hashtext('browser-maintenance')
                    )
                    """
                )
            )
        row = (
            await conn.execute(
                _CLAIM_SQL,
                {
                    "tt": task_type,
                    "lanes": effective_lanes,
                    "worker_id": worker_id or _DEFAULT_WORKER_ID,
                    "lease_seconds": max(5, int(lease_seconds)),
                },
            )
        ).first()
        if row is not None:
            await _transition_correlated_incident(
                conn,
                task_id=int(row.id),
                correlation_id=_optional_uuid(row.correlation_id),
                phase="executing",
                payload=dict(row.payload or {}),
            )
    metric_lane = (
        str(row.lane)
        if row is not None
        else (effective_lanes[0] if len(effective_lanes) == 1 else "general")
    )
    TASK_CLAIM_LATENCY.labels(lane=metric_lane, task_type=task_type).observe(
        time.perf_counter() - started_at
    )
    if not row:
        return TaskClaim(task=None, queue_empty=True)
    return TaskClaim(task=_row_to_task(row), queue_empty=False)


async def claim_browser_ready_task(
    engine: AsyncEngine,
    *,
    task_type: str,
    lanes: Sequence[str],
    worker_id: uuid.UUID | None = None,
    lease_seconds: int = 30 * 60,
) -> TaskClaim:
    """Claim only with fresh v5 evidence for the canonical Vision profile.

    The evidence is a scheduling gate, not operation authority.  The caller
    must still use ``MetaApiClient.operation_authority`` so every controlled
    RPC performs the exact live check and one-shot capability consume.
    """
    if task_type not in BROWSER_READY_CLAIM_TASK_TYPES:
        raise ValueError("claim_browser_ready_task authorizes only Meta mutation task types")
    effective_lanes = tuple(lanes)
    if not effective_lanes or any(lane not in TASK_LANES for lane in effective_lanes):
        raise ValueError("claim_browser_ready_task requires valid non-empty lanes")
    started_at = time.perf_counter()
    async with engine.begin() as conn:
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        await conn.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(
                  hashtext('fb-agent'),
                  hashtext('browser-maintenance')
                )
                """
            )
        )
        row = (
            await conn.execute(
                _BROWSER_READY_CLAIM_SQL,
                {
                    "tt": task_type,
                    "lanes": effective_lanes,
                    "worker_id": worker_id or _DEFAULT_WORKER_ID,
                    "lease_seconds": max(5, int(lease_seconds)),
                },
            )
        ).first()
        if row is not None:
            await _transition_correlated_incident(
                conn,
                task_id=int(row.id),
                correlation_id=_optional_uuid(row.correlation_id),
                phase="executing",
                payload=dict(row.payload or {}),
            )
    metric_lane = (
        str(row.lane)
        if row is not None
        else (effective_lanes[0] if len(effective_lanes) == 1 else "general")
    )
    TASK_CLAIM_LATENCY.labels(
        lane=metric_lane,
        task_type=task_type,
    ).observe(time.perf_counter() - started_at)
    if row is None:
        return TaskClaim(task=None, queue_empty=True)
    task = _row_to_task(row)
    task.browser_profile_id = str(row.browser_profile_id)
    task.browser_readiness_generation = int(row.browser_readiness_generation)
    return TaskClaim(
        task=task,
        queue_empty=False,
        browser_profile_id=task.browser_profile_id,
        browser_readiness_generation=task.browser_readiness_generation,
    )


# ====================== finalize ======================


async def mark_succeeded(
    engine: AsyncEngine,
    *,
    task_id: int,
    result: dict[str, Any] | None = None,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
    transactional_effect: Callable[[AsyncConnection], Awaitable[None]] | None = None,
) -> bool:
    """Финальный статус: задача выполнена успешно.

    Returns: True если status был 'running' и переведён в 'succeeded'.
    False — update не применился (status уже не 'running'): обычно это race
    с другим воркером, который уже закрыл задачу после reconciler-таймаута.
    Caller обязан залогировать и пропустить любые побочные эффекты.

    ``transactional_effect`` выполняется только после успешного fenced UPDATE,
    но до COMMIT и incident/outbox transitions. Любая его ошибка откатывает и
    terminal status, и effect — для доменных записей, которые обязаны появиться
    атомарно с подтверждённой задачей.
    """
    if not _has_valid_fence(lease_owner, lease_token):
        logger.error("mark_succeeded refused task_id=%s without a valid lease fence", task_id)
        return False
    async with engine.begin() as conn:
        result_obj = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'succeeded',
                    result = CAST(:res AS JSONB),
                    completed_at = NOW(),
                    last_error = NULL,
                    updated_at = NOW()
                WHERE id = :id AND status = 'running'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                "id": int(task_id),
                "res": json.dumps(result or {}),
                "lease_owner": lease_owner,
                "lease_token": lease_token,
            },
        )
        if (result_obj.rowcount or 0) > 0:
            if transactional_effect is not None:
                await transactional_effect(conn)
            task = (
                await conn.execute(
                    text(
                        """
                        SELECT correlation_id, payload, requested_by, lane, task_type
                        FROM task_queue
                        WHERE id = :task_id
                        """
                    ),
                    {"task_id": int(task_id)},
                )
            ).first()
            if task is not None:
                result_payload = result or {}
                phase: Literal["confirmed", "unknown"] = (
                    "unknown"
                    if result_payload.get("outcome") == "UNKNOWN"
                    or result_payload.get("reconcile_required") is True
                    else "confirmed"
                )
                await _transition_correlated_incident(
                    conn,
                    task_id=task_id,
                    correlation_id=_optional_uuid(_returned_value(task, "correlation_id")),
                    phase=phase,
                    payload=dict(_returned_value(task, "payload") or {}),
                )
                task_payload = dict(_returned_value(task, "payload") or {})
                if _is_partial_money_result(task_payload, result_payload):
                    await _enqueue_standalone_money_failure(
                        conn,
                        task_id=task_id,
                        correlation_id=_optional_uuid(_returned_value(task, "correlation_id")),
                        phase="failed",
                        payload=task_payload,
                        requested_by=str(_returned_value(task, "requested_by") or ""),
                        lane=str(_returned_value(task, "lane") or ""),
                        task_type=str(_returned_value(task, "task_type") or ""),
                        dedupe_suffix="partial",
                    )
                await _resolve_confirmed_autostop_incidents_in_transaction(
                    conn,
                    payload=task_payload,
                    result=result_payload,
                    requested_by=str(_returned_value(task, "requested_by") or ""),
                )
                await _resolve_meta_token_incident_in_transaction(
                    conn,
                    task_type=str(_returned_value(task, "task_type") or ""),
                )
    return (result_obj.rowcount or 0) > 0


async def mark_failed(
    engine: AsyncEngine,
    *,
    task_id: int,
    error: str,
    result: dict[str, Any] | None = None,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
    transactional_effect: Callable[[AsyncConnection], Awaitable[None]] | None = None,
) -> bool:
    """Финальный статус: задача провалена окончательно (исчерпан max_attempts).

    Если attempts < max_attempts — используй requeue_for_retry, не mark_failed.

    result задаёт структурированный JSONB результата ошибки в том же атомарном
    UPDATE (например created_ids partial mutation). Если result не передан,
    уже существующий task_queue.result сохраняется без изменений.
    ``transactional_effect`` выполняется после fenced UPDATE и до incident/outbox
    projection. Ошибка откатывает terminal task и доменный эффект целиком.

    Returns: True если status был 'running' и переведён в 'failed'.
    False — update не применился (status уже не 'running'): race с воркером,
    который успел закрыть задачу. Caller должен залогировать.
    """
    if not _has_valid_fence(lease_owner, lease_token):
        logger.error("mark_failed refused task_id=%s without a valid lease fence", task_id)
        return False
    async with engine.begin() as conn:
        update_result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'failed',
                    last_error = :err,
                    result = COALESCE(CAST(:res AS JSONB), result),
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE id = :id AND status = 'running'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                "id": int(task_id),
                "err": error[:8000],
                "res": json.dumps(result) if result is not None else None,
                "lease_owner": lease_owner,
                "lease_token": lease_token,
            },
        )
        if (update_result.rowcount or 0) > 0:
            if transactional_effect is not None:
                await transactional_effect(conn)
            task = (
                await conn.execute(
                    text(
                        """
                        SELECT correlation_id, payload, result, requested_by, lane, task_type
                        FROM task_queue
                        WHERE id = :task_id
                        """
                    ),
                    {"task_id": int(task_id)},
                )
            ).first()
            if task is not None:
                stored_result = _returned_value(task, "result")
                task_result = stored_result if isinstance(stored_result, dict) else {}
                task_payload = dict(_returned_value(task, "payload") or {})
                await _project_duplicate_terminal_incident(
                    conn,
                    task_id=task_id,
                    payload=task_payload,
                    result=task_result,
                )
                phase: Literal["failed", "unknown"] = (
                    "unknown"
                    if task_result.get("outcome") == "UNKNOWN"
                    or task_result.get("reconcile_required") is True
                    else "failed"
                )
                await _transition_terminal_task(
                    conn,
                    task_id=task_id,
                    correlation_id=_optional_uuid(_returned_value(task, "correlation_id")),
                    phase=phase,
                    payload=task_payload,
                    requested_by=str(_returned_value(task, "requested_by") or ""),
                    lane=str(_returned_value(task, "lane") or ""),
                    task_type=str(_returned_value(task, "task_type") or ""),
                )
                await _project_meta_token_incident_in_transaction(
                    conn,
                    task_type=str(_returned_value(task, "task_type") or ""),
                    result=task_result,
                )
        return (update_result.rowcount or 0) > 0


async def mark_cancelled(
    engine: AsyncEngine,
    *,
    task_id: int,
    reason: str,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
) -> bool:
    """Terminally cancel only the current fenced owner before external send."""
    if not _has_valid_fence(lease_owner, lease_token):
        logger.error("mark_cancelled refused task_id=%s without a valid lease fence", task_id)
        return False
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'cancelled', completed_at = NOW(),
                    last_error = :reason,
                    result = COALESCE(result, '{}'::jsonb)
                        || jsonb_build_object('outcome', 'REJECTED',
                                              'reason', 'cancel_requested'),
                    lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = :task_id AND status = 'running'
                  AND external_started_at IS NULL
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                "task_id": int(task_id),
                "reason": reason[:8000],
                "lease_owner": lease_owner,
                "lease_token": lease_token,
            },
        )
        if (result.rowcount or 0) > 0:
            task = (
                await conn.execute(
                    text(
                        """
                        SELECT correlation_id, payload
                        FROM task_queue
                        WHERE id = :task_id
                        """
                    ),
                    {"task_id": int(task_id)},
                )
            ).first()
            if task is not None:
                await _transition_correlated_incident(
                    conn,
                    task_id=task_id,
                    correlation_id=_optional_uuid(_returned_value(task, "correlation_id")),
                    phase="cancelled",
                    payload=dict(_returned_value(task, "payload") or {}),
                )
    return bool(result.rowcount)


async def resolve_status_reconciliation_not_applied(
    engine: AsyncEngine,
    *,
    task_id: int,
    effective_status: str,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
) -> str | None:
    """Atomically finish an UNKNOWN status read when the write was not applied.

    If cancellation won while the verification GET was in flight, the command
    becomes terminal only now, after Meta proved the requested state was not
    applied.  Otherwise the previous external boundary is cleared and exactly
    one idempotent resend may proceed while the write-attempt budget remains.
    The final attempt becomes explicit ``REJECTED`` after the read proves it was
    not applied.  The single UPDATE closes the race between cancellation, retry
    exhaustion and preparing a resend.
    """
    if not _has_valid_fence(lease_owner, lease_token):
        logger.error(
            "resolve_status_reconciliation_not_applied refused task_id=%s without a valid fence",
            task_id,
        )
        return None
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN 'cancelled'
                        WHEN attempt_count >= max_attempts THEN 'failed'
                        ELSE 'running'
                    END,
                    completed_at = CASE
                        WHEN cancel_requested_at IS NOT NULL
                          OR attempt_count >= max_attempts
                            THEN NOW()
                        ELSE NULL
                    END,
                    external_started_at = CASE
                        WHEN cancel_requested_at IS NOT NULL
                          OR attempt_count >= max_attempts
                            THEN external_started_at
                        ELSE NULL
                    END,
                    result = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN
                            (COALESCE(result, '{}'::jsonb) - 'reconcile_required')
                            || jsonb_build_object(
                                'outcome', 'REJECTED',
                                'reason', 'cancelled_after_verified_not_applied',
                                'reconciled_after_unknown', true,
                                'effective_status', CAST(:effective_status AS TEXT)
                            )
                        WHEN attempt_count >= max_attempts THEN
                            (COALESCE(result, '{}'::jsonb) - 'reconcile_required')
                            || jsonb_build_object(
                                'outcome', 'REJECTED',
                                'reason',
                                    'attempts_exhausted_after_verified_not_applied',
                                'reconciled_after_unknown', true,
                                'reconcile_required', false,
                                'effective_status', CAST(:effective_status AS TEXT)
                            )
                        ELSE
                            (COALESCE(result, '{}'::jsonb)
                                - 'reconcile_required' - 'outcome')
                            || jsonb_build_object(
                                'reconciled_not_applied', true,
                                'effective_status', CAST(:effective_status AS TEXT)
                            )
                    END,
                    last_error = CASE
                        WHEN cancel_requested_at IS NOT NULL
                            THEN COALESCE(cancel_reason, 'cancelled after reconciliation')
                        WHEN attempt_count >= max_attempts
                            THEN 'status write was verified not applied; attempts exhausted'
                        ELSE NULL
                    END,
                    lease_owner = CASE
                        WHEN cancel_requested_at IS NOT NULL
                          OR attempt_count >= max_attempts
                            THEN NULL
                        ELSE lease_owner
                    END,
                    lease_expires_at = CASE
                        WHEN cancel_requested_at IS NOT NULL
                          OR attempt_count >= max_attempts
                            THEN NULL
                        ELSE lease_expires_at
                    END,
                    updated_at = NOW()
                WHERE id = :task_id
                  AND status = 'running'
                  AND external_started_at IS NOT NULL
                  AND COALESCE(result->>'reconcile_required', 'false') = 'true'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                RETURNING id, correlation_id, payload, status, result,
                          requested_by, lane, task_type
                """
            ),
            {
                "task_id": int(task_id),
                "effective_status": effective_status[:100],
                "lease_owner": lease_owner,
                "lease_token": lease_token,
            },
        )
        row = result.first()
        if row is not None:
            await _transition_returned_terminal_tasks(conn, [row])
    return str(_returned_value(row, "status")) if row is not None else None


async def touch_task_running(
    engine: AsyncEngine,
    *,
    task_id: int,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
    lease_seconds: int = 30 * 60,
) -> bool:
    """Extend the current fenced lease for a running task.

    ``updated_at`` remains useful for operator diagnostics, but the reconciler
    only treats an owned task as abandoned after ``lease_expires_at``.  A stale
    process therefore cannot keep or finish work after another owner has claimed
    the next fencing token.
    """
    if not _has_valid_fence(lease_owner, lease_token):
        logger.error("touch_task_running refused task_id=%s without a valid lease fence", task_id)
        return False
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET updated_at = NOW(),
                    lease_expires_at = NOW() + make_interval(secs => :lease_seconds)
                WHERE id = :id AND status = 'running'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                "id": int(task_id),
                "lease_owner": lease_owner,
                "lease_token": lease_token,
                "lease_seconds": max(5, int(lease_seconds)),
            },
        )
    return (result.rowcount or 0) > 0


async def checkpoint_duplicate_adset_structure(
    engine: AsyncEngine,
    *,
    task_id: int,
    checkpoint: dict[str, Any],
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
) -> bool:
    """Persist one crash-safe duplicate progress boundary.

    This writer is intentionally scoped to ``duplicate_adset_structure``. Once
    the reconciler sets ``recovery_requested=true``, the original worker is no
    longer allowed to advance the checkpoint even if its stale Graph call later
    returns and another worker has already claimed the recovery task.
    """
    if checkpoint.get("checkpoint_type") != "duplicate_adset_structure":
        raise ValueError("invalid duplicate checkpoint type")
    if not _has_valid_fence(lease_owner, lease_token):
        logger.error(
            "checkpoint_duplicate_adset_structure refused task_id=%s without a valid lease fence",
            task_id,
        )
        return False
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET result = CAST(:checkpoint AS JSONB),
                    updated_at = NOW()
                WHERE id = :id
                  AND task_type = 'meta_api_mutation'
                  AND status = 'running'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                  AND payload->>'mutation_kind' = 'duplicate_adset_structure'
                  AND COALESCE(result->>'recovery_requested', 'false') <> 'true'
                """
            ),
            {
                "id": int(task_id),
                "checkpoint": json.dumps(checkpoint),
                "lease_owner": lease_owner,
                "lease_token": lease_token,
            },
        )
    return (result.rowcount or 0) > 0


async def prepare_stuck_duplicate_recovery(
    engine: AsyncEngine,
    *,
    stuck_after_seconds: int = 1800,
) -> int:
    """Move stale checkpointed ad-set duplicates into recovery-only execution.

    The next meta worker must only PAUSE ``result.created_ids``; it must never
    replay creation. This transition runs before the generic irreversible fail
    pass. A recovery flag also fences a stale original worker from persisting
    further creation progress after this transition. The critical incident and
    notification event are committed in this same transaction.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    available_at = NOW(),
                    deadline_at = NOW() + make_interval(
                        secs => CASE lane
                            WHEN 'money' THEN 30
                            WHEN 'bulk' THEN 1800
                            ELSE 120
                        END
                    ),
                    last_error = COALESCE(last_error, '')
                        || ' [stuck duplicate_adset_structure: scheduled PAUSE recovery]',
                    result = result || jsonb_build_object(
                        'recovery_requested', true,
                        'phase', 'recovery_pending'
                    ),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE task_type = 'meta_api_mutation'
                  AND status = 'running'
                  AND (
                      (lease_expires_at IS NOT NULL AND lease_expires_at <= NOW())
                      OR (
                          lease_expires_at IS NULL
                          AND updated_at < NOW() - make_interval(secs => :sec)
                      )
                  )
                  AND payload->>'mutation_kind' = 'duplicate_adset_structure'
                  AND result->>'checkpoint_type' = 'duplicate_adset_structure'
                  AND jsonb_typeof(result->'created_ids') = 'object'
                RETURNING id, result
                """
            ),
            {"sec": int(stuck_after_seconds)},
        )
        rows, count = _returned_task_rows(result)
        if rows:
            from core.meta_api.duplicate_incidents import (
                project_duplicate_incident_in_transaction,
            )

            for row in rows:
                checkpoint = _returned_value(row, "result")
                await project_duplicate_incident_in_transaction(
                    conn,
                    task_id=int(_returned_value(row, "id")),
                    checkpoint=checkpoint if isinstance(checkpoint, dict) else {},
                    stage="recovery_scheduled",
                )
    if count:
        logger.error(
            "reconcile: %d stale duplicate_adset_structure task(s) scheduled for PAUSE recovery",
            count,
        )
    return count


async def requeue_duplicate_recovery(
    engine: AsyncEngine,
    *,
    task_id: int,
    checkpoint: dict[str, Any],
    error: str,
    delay_seconds: int = 60,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
) -> bool:
    """Retry PAUSE-only recovery without replaying the original create plan.

    Original duplicate drafts use ``max_attempts=1``. Cleanup retries therefore
    have an independent, money-safe lifecycle and remain retryable until every
    checkpointed object has been paused. Every applied fenced transition also
    refreshes the per-task critical incident before commit.
    """
    if checkpoint.get("recovery_requested") is not True:
        raise ValueError("invalid duplicate recovery checkpoint")
    # The final cleanup checkpoint can be the first durable checkpoint when an
    # earlier progress write failed after Meta had already returned created IDs.
    # Validate the complete recovery payload before allowing it to replace a
    # missing/stale result; the live lease fence below remains authoritative.
    from core.meta_api.mutations.duplicate_adset_structure import (
        DuplicateAdsetStructureHandler,
    )

    DuplicateAdsetStructureHandler.created_ids_from_checkpoint(checkpoint)
    if not _has_valid_fence(lease_owner, lease_token):
        logger.error(
            "requeue_duplicate_recovery refused task_id=%s without a valid lease fence",
            task_id,
        )
        return False
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    available_at = NOW() + make_interval(secs => :sec),
                    deadline_at = NOW() + make_interval(
                        secs => :sec + CASE lane
                            WHEN 'money' THEN 30
                            WHEN 'bulk' THEN 1800
                            ELSE 120
                        END
                    ),
                    last_error = :err,
                    result = CAST(:checkpoint AS JSONB),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = :id
                  AND task_type = 'meta_api_mutation'
                  AND status = 'running'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                  AND payload->>'mutation_kind' = 'duplicate_adset_structure'
                """
            ),
            {
                "id": int(task_id),
                "sec": max(1, int(delay_seconds)),
                "err": error[:8000],
                "checkpoint": json.dumps(checkpoint),
                "lease_owner": lease_owner,
                "lease_token": lease_token,
            },
        )
        if (result.rowcount or 0) > 0:
            from core.meta_api.duplicate_incidents import (
                duplicate_requeue_stage,
                project_duplicate_incident_in_transaction,
            )

            await project_duplicate_incident_in_transaction(
                conn,
                task_id=task_id,
                checkpoint=checkpoint,
                stage=duplicate_requeue_stage(checkpoint),
            )
    return (result.rowcount or 0) > 0


async def mark_external_call_started(
    engine: AsyncEngine,
    *,
    task_id: int,
    target_lock_key: str,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
) -> bool:
    """Зафиксировать необратимую границу непосредственно перед внешним вызовом.

    Tracker-worker берёт тот же advisory lock перед отменой автоматического
    ``pause_ad``. Пока ``external_started_at`` равен NULL, положительный postback
    ещё может безопасно отменить задачу. После первого входа во внешний вызов
    значение сохраняется на всех retry: запрос мог уйти, даже если локальный
    ответ потерян. Повторная попытка только освежает ``updated_at``.

    Returns:
        True, если задача всё ещё ``running`` и граница зафиксирована. False,
        если tracker или другой исполнитель уже закрыл/отменил задачу.
    """
    if not target_lock_key:
        raise ValueError("target_lock_key must not be empty")
    if not _has_valid_fence(lease_owner, lease_token):
        logger.error(
            "mark_external_call_started refused task_id=%s without a valid lease fence",
            task_id,
        )
        return False

    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": target_lock_key},
        )
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET external_started_at = COALESCE(external_started_at, NOW()),
                    updated_at = NOW()
                WHERE id = :id
                  AND status = 'running'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                  AND cancel_requested_at IS NULL
                  AND (deadline_at IS NULL OR deadline_at > clock_timestamp())
                """
            ),
            {
                "id": int(task_id),
                "lease_owner": lease_owner,
                "lease_token": lease_token,
            },
        )
    return (result.rowcount or 0) > 0


async def requeue_for_retry(
    engine: AsyncEngine,
    *,
    task_id: int,
    error: str,
    attempt_count: int,
    max_attempts: int,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
    lane: str = "background",
) -> bool:
    """Решает: ещё retry или окончательный failed?

    Returns: True если retry поставлен (status='retrying').
    False — либо final failed (mark_failed), либо update не применился
    из-за race с другим воркером, который уже завершил задачу.
    Caller'у достаточно различать «retry vs не-retry», тонкая разница
    «final fail vs noop» уже отражена в БД (status='succeeded' остался).
    """
    if not _has_valid_fence(lease_owner, lease_token):
        logger.error("requeue_for_retry refused task_id=%s without a valid lease fence", task_id)
        return False
    new_attempt = attempt_count + 1
    if new_attempt >= max_attempts:
        applied = await mark_failed(
            engine,
            task_id=task_id,
            error=error,
            result={"outcome": "REJECTED"},
            lease_owner=lease_owner,
            lease_token=lease_token,
        )
        if not applied:
            logger.warning(
                "requeue_for_retry: task_id=%s mark_failed не применился "
                "(status != running) — гонка с другим воркером, пропускаю",
                task_id,
            )
        return False

    next_at = _calc_retry_available_at(new_attempt)
    retry_deadline = (
        None
        if lane == "money"
        else next_at
        + timedelta(
            seconds=_LANE_DEFAULT_DEADLINE_SECONDS.get(
                lane, _LANE_DEFAULT_DEADLINE_SECONDS["background"]
            )
        )
    )
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    attempt_count = :n,
                    available_at = :available_at,
                    deadline_at = :deadline_at,
                    last_error = :err,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = :id AND status = 'running'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                "id": int(task_id),
                "n": new_attempt,
                "available_at": next_at,
                "deadline_at": retry_deadline,
                "err": error[:8000],
                "lease_owner": lease_owner,
                "lease_token": lease_token,
            },
        )
        applied = (result.rowcount or 0) > 0
    if not applied:
        logger.warning(
            "requeue_for_retry: task_id=%s переход в retrying не применился "
            "(status/lease изменились или lease истёк) — пропускаю",
            task_id,
        )
    return applied


async def requeue_proven_not_committed(
    engine: AsyncEngine,
    *,
    task_id: int,
    target_lock_key: str,
    error: str,
    attempt_count: int,
    max_attempts: int,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
    lane: str = "background",
) -> str | None:
    """Close a proven pre-send attempt without retaining an ambiguous boundary.

    ``external_started_at`` is deliberately conservative: it is written before
    entering browser-agent because most transport failures cannot prove whether
    Meta received the request. ``SessionUnavailableError`` is the narrow
    exception: browser-agent rejected the operation before issuing the fetch.

    Tracker projection and this transition serialize on the same per-target
    advisory lock. A positive event that wins first leaves a durable
    ``cancel_requested_at`` marker; this transition then cancels instead of
    requeueing. If this transition wins first it clears the boundary, so the
    tracker transaction that follows can terminally cancel the retry.

    Returns the applied status (``retrying``, ``cancelled`` or ``failed``), or
    ``None`` when the lease fence no longer owns the task.
    """
    if not target_lock_key:
        raise ValueError("target_lock_key must not be empty")
    if not _has_valid_fence(lease_owner, lease_token):
        logger.error(
            "requeue_proven_not_committed refused task_id=%s without a valid lease fence",
            task_id,
        )
        return None

    new_attempt = int(attempt_count) + 1
    next_at = _calc_retry_available_at(new_attempt)
    retry_deadline = (
        None
        if lane == "money"
        else next_at
        + timedelta(
            seconds=_LANE_DEFAULT_DEADLINE_SECONDS.get(
                lane, _LANE_DEFAULT_DEADLINE_SECONDS["background"]
            )
        )
    )
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": target_lock_key},
        )
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN 'cancelled'
                        WHEN attempt_count + 1 >= max_attempts THEN 'failed'
                        ELSE 'retrying'
                    END,
                    attempt_count = CASE
                        WHEN cancel_requested_at IS NOT NULL
                            THEN attempt_count
                        ELSE attempt_count + 1
                    END,
                    available_at = CASE
                        WHEN cancel_requested_at IS NULL
                         AND attempt_count + 1 < max_attempts
                            THEN :available_at
                        ELSE available_at
                    END,
                    deadline_at = CASE
                        WHEN cancel_requested_at IS NULL
                         AND attempt_count + 1 < max_attempts
                            THEN :deadline_at
                        ELSE deadline_at
                    END,
                    completed_at = CASE
                        WHEN cancel_requested_at IS NOT NULL
                          OR attempt_count + 1 >= max_attempts
                            THEN NOW()
                        ELSE NULL
                    END,
                    external_started_at = NULL,
                    last_error = CASE
                        WHEN cancel_requested_at IS NOT NULL
                            THEN COALESCE(cancel_reason, 'cancelled after proven pre-send reject')
                        ELSE :err
                    END,
                    result = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN
                            (COALESCE(result, '{}'::jsonb)
                                - 'reconcile_required' - 'manual_review_required')
                            || jsonb_build_object(
                                'outcome', 'REJECTED',
                                'reason', 'cancelled_after_proven_not_committed'
                            )
                        WHEN attempt_count + 1 >= max_attempts THEN
                            (COALESCE(result, '{}'::jsonb)
                                - 'reconcile_required' - 'manual_review_required')
                            || jsonb_build_object(
                                'outcome', 'REJECTED',
                                'reason', 'attempts_exhausted_proven_not_committed'
                            )
                        ELSE result
                    END,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = :id
                  AND status = 'running'
                  AND external_started_at IS NOT NULL
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                  AND payload->>'target_id' = :target_lock_key
                RETURNING id, correlation_id, payload, status, result,
                          requested_by, lane, task_type
                """
            ),
            {
                "id": int(task_id),
                "target_lock_key": target_lock_key,
                "available_at": next_at,
                "deadline_at": retry_deadline,
                "err": error[:8000],
                "lease_owner": lease_owner,
                "lease_token": lease_token,
            },
        )
        row = result.first()
        if row is not None:
            await _transition_returned_terminal_tasks(conn, [row])
    return str(_returned_value(row, "status")) if row is not None else None


async def release_after_browser_readiness_rejection(
    engine: AsyncEngine,
    *,
    task: Task,
    error: str,
    target_lock_key: str | None = None,
    transactional_effect: (Callable[[AsyncConnection, str], Awaitable[None]] | None) = None,
) -> str | None:
    """Release a live-identity rejection without consuming an attempt.

    The exact per-RPC check proves that browser-agent did not issue the
    controlled request. The rejected readiness generation has already been
    CAS-expired by ``MetaApiClient``; this transition only returns the task to
    the queue. The next claim assigns a fresh execution deadline.
    """
    if (
        not _has_valid_fence(task.lease_owner, task.lease_token)
        or task.browser_readiness_generation is None
        or task.browser_readiness_generation <= 0
    ):
        logger.error(
            "browser readiness rejection refused task_id=%s without gated lease evidence",
            task.id,
        )
        return None
    async with engine.begin() as conn:
        if target_lock_key is not None:
            normalized_target = target_lock_key.strip()
            if not normalized_target:
                raise ValueError("target_lock_key must not be empty")
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": normalized_target},
            )
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = CASE
                        WHEN cancel_requested_at IS NOT NULL
                          THEN 'cancelled'
                        ELSE 'retrying'
                    END,
                    available_at = CASE
                        WHEN cancel_requested_at IS NULL
                          THEN clock_timestamp()
                        ELSE available_at
                    END,
                    deadline_at = CASE
                        WHEN cancel_requested_at IS NULL
                          THEN CASE
                            WHEN lane = 'money' THEN NULL
                            ELSE clock_timestamp()
                              + make_interval(secs => :deadline_seconds)
                          END
                        ELSE deadline_at
                    END,
                    completed_at = CASE
                        WHEN cancel_requested_at IS NOT NULL
                          THEN clock_timestamp()
                        ELSE NULL
                    END,
                    external_started_at = NULL,
                    last_error = CASE
                        WHEN cancel_requested_at IS NOT NULL
                          THEN COALESCE(
                            cancel_reason,
                            'cancelled after browser readiness rejection'
                          )
                        ELSE :error
                    END,
                    result = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN
                          (
                            COALESCE(result, '{}'::jsonb)
                              - 'external_boundary_operation'
                              - 'external_boundary_target'
                              - 'reconcile_required'
                              - 'manual_review_required'
                          ) || jsonb_build_object(
                            'outcome', 'REJECTED',
                            'reason',
                            'cancelled_after_browser_readiness_rejection'
                          )
                        ELSE
                          COALESCE(result, '{}'::jsonb)
                            - 'external_boundary_operation'
                            - 'external_boundary_target'
                    END,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = clock_timestamp()
                WHERE id = :task_id
                  AND status = 'running'
                  AND lease_owner = :lease_owner
                  AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                RETURNING id, correlation_id, payload, status, result,
                          requested_by, lane, task_type
                """
            ),
            {
                "task_id": task.id,
                "error": error[:8000],
                "deadline_seconds": _LANE_DEFAULT_DEADLINE_SECONDS.get(
                    task.lane,
                    _LANE_DEFAULT_DEADLINE_SECONDS["background"],
                ),
                "lease_owner": task.lease_owner,
                "lease_token": task.lease_token,
            },
        )
        row = result.first()
        if row is not None:
            applied_status = str(_returned_value(row, "status"))
            if transactional_effect is not None:
                await transactional_effect(conn, applied_status)
            await _transition_returned_terminal_tasks(conn, [row])
    return str(_returned_value(row, "status")) if row is not None else None


async def requeue_unknown_for_reconciliation(
    engine: AsyncEngine,
    *,
    task: Task,
    error: str,
) -> bool:
    """Schedule an immediate read-after-ambiguous-write verification.

    The next claim must reconcile effective status before it is allowed to send
    the mutation again.  A fresh per-attempt deadline bounds that verification.
    """
    next_attempt = task.attempt_count + 1
    already_reconciling = (
        isinstance(task.result, dict) and task.result.get("reconcile_required") is True
    )
    # A write that became ambiguous on the nominal last attempt still gets one
    # read-only verification claim.  Only an already-running reconciliation
    # may exhaust the budget and become terminal UNKNOWN.
    if already_reconciling and next_attempt >= task.max_attempts:
        await mark_failed(
            engine,
            task_id=task.id,
            error=error,
            result={
                "outcome": "UNKNOWN",
                "reconcile_required": False,
                "reconciliation_exhausted": True,
            },
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        return False
    deadline = (
        None
        if task.lane == "money"
        else datetime.now(timezone.utc)
        + timedelta(seconds=_LANE_DEFAULT_DEADLINE_SECONDS.get(task.lane, 120))
    )
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying', attempt_count = :attempt_count,
                    available_at = NOW(),
                    deadline_at = :deadline_at, last_error = :error,
                    result = COALESCE(result, '{}'::jsonb) || jsonb_build_object(
                        'outcome', 'UNKNOWN',
                        'reconcile_required', true,
                        'unknown_at', NOW()
                    ),
                    lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = :task_id AND status = 'running'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                "task_id": task.id,
                "attempt_count": next_attempt,
                "deadline_at": deadline,
                "error": error[:8000],
                "lease_owner": task.lease_owner,
                "lease_token": task.lease_token,
            },
        )
    return bool(result.rowcount)


async def defer_unknown_reconciliation(
    engine: AsyncEngine,
    *,
    task: Task,
    error: str,
    delay_seconds: int = 1,
) -> bool:
    """Defer a read-only reconciliation when its target mutex is busy.

    Lock contention is not a mutation attempt and must never consume the final
    verification budget.  Keeping the child non-terminal also keeps every
    dependency-gated observer scan parked.
    """
    if not _has_valid_fence(task.lease_owner, task.lease_token):
        return False
    delay = max(1, int(delay_seconds))
    deadline_seconds = _LANE_DEFAULT_DEADLINE_SECONDS.get(task.lane, 120)
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    available_at = NOW() + make_interval(secs => :delay_seconds),
                    deadline_at = CASE
                        WHEN lane = 'money' THEN NULL
                        ELSE NOW() + make_interval(
                            secs => :deadline_delay_seconds
                        )
                    END,
                    last_error = :error,
                    result = COALESCE(result, '{}'::jsonb)
                        || jsonb_build_object(
                            'outcome', 'UNKNOWN',
                            'reconcile_required', true
                        ),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = :task_id
                  AND status = 'running'
                  AND lease_owner = :lease_owner
                  AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                "task_id": int(task.id),
                "delay_seconds": delay,
                "deadline_delay_seconds": delay + deadline_seconds,
                "error": error[:8000],
                "lease_owner": task.lease_owner,
                "lease_token": task.lease_token,
            },
        )
    return bool(result.rowcount)


# ====================== reconcile (вызывается reconciler_worker'ом) ======================


async def fail_stuck_duplicate_without_checkpoint(
    engine: AsyncEngine,
    *,
    stuck_after_seconds: int = 1800,
) -> int:
    """Fail a stale duplicate only when no recoverable created-ID checkpoint exists.

    Creation calls always request PAUSED objects. Therefore an early crash before
    the first persisted ID cannot create spend, while replaying the plan could
    create duplicates. Checkpointed tasks are handled separately by
    :func:`prepare_stuck_duplicate_recovery` and must never reach this UPDATE.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'failed',
                    completed_at = NOW(),
                    last_error = COALESCE(last_error, '')
                        || ' [stuck duplicate_adset_structure without recoverable checkpoint: '
                        || 'НЕ ретраим; созданные до checkpoint объекты были requested PAUSED]',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    result = COALESCE(result, '{}'::jsonb) || jsonb_build_object(
                        'outcome', 'UNKNOWN',
                        'reconcile_required', true,
                        'reason', 'stuck_duplicate_without_checkpoint'
                    ),
                    updated_at = NOW()
                WHERE task_type = 'meta_api_mutation'
                  AND status = 'running'
                  AND (
                      (lease_expires_at IS NOT NULL AND lease_expires_at <= NOW())
                      OR (
                          lease_expires_at IS NULL
                          AND updated_at < NOW() - make_interval(secs => :sec)
                      )
                  )
                  AND payload->>'mutation_kind' = 'duplicate_adset_structure'
                  AND NOT (
                      COALESCE(result->>'checkpoint_type', '') = 'duplicate_adset_structure'
                      AND COALESCE(jsonb_typeof(result->'created_ids'), '') = 'object'
                  )
                RETURNING id, correlation_id, payload, status, result,
                          requested_by, lane, task_type
                """
            ),
            {"sec": int(stuck_after_seconds)},
        )
        rows, count = _returned_task_rows(result)
        if rows:
            from core.meta_api.duplicate_incidents import (
                project_duplicate_incident_in_transaction,
            )

            for row in rows:
                stored_result = _returned_value(row, "result")
                await project_duplicate_incident_in_transaction(
                    conn,
                    task_id=int(_returned_value(row, "id")),
                    checkpoint=stored_result if isinstance(stored_result, dict) else {},
                    stage="checkpoint_missing",
                )
    if count:
        logger.error(
            "reconcile: %d stale duplicate_adset_structure task(s) without checkpoint "
            "failed without retry",
            count,
        )
    return count


# task_type, которые НЕЛЬЗЯ слепо ретраить при зависании в 'running' — необратимое
# создание объектов в Meta (повтор = дубль кампании + двойной открут бюджета). Их
# зависшие строки уводит в failed через fail_stuck_campaign_create
# (НЕ retrying). Зеркалит контракт IRREVERSIBLE_MUTATION_KINDS для meta_api_mutation,
# на уровне task_type.
IRREVERSIBLE_TASK_TYPES: frozenset[str] = frozenset({"campaign_create"})


async def fail_stuck_campaign_create(
    engine: AsyncEngine,
    *,
    stuck_after_seconds: int = 1800,
) -> int:
    """Зависшие в 'running' задачи task_type='campaign_create' → 'failed' (НЕ retry).

    Money-safety for the dedicated campaign task type. campaign_creator_worker
    начал создавать кампанию в Meta
    (POST /campaigns/adsets/...) и умер (SIGKILL/OOM/деплой) ДО mark_succeeded → задача
    застряла в 'running'. Слепой reconcile перевёл бы её в 'retrying' → повторный залив =
    ДУБЛЬ кампании + двойной открут бюджета. Помечаем 'failed' с явным error — оператор
    проверяет Meta вручную (осиротевшие объекты в campaign_run.created_meta_ids, если
    воркер успел их записать; иначе — по кабинету).

    Вызывать ПЕРЕД reconcile_stuck_running (тот безусловно исключает campaign_create —
    двойная защита). UNKNOWN incident и notification event создаются в этой же
    транзакции для каждой строки; caller получает только счётчик для метрик.
    """
    stmt = text(
        """
        UPDATE task_queue
        SET status = 'failed',
            completed_at = NOW(),
            last_error = COALESCE(last_error, '')
                || ' [stuck campaign_create: воркер мог начать залив в Meta до краша '
                || '— НЕ ретраим (риск дубля кампании), проверь Meta вручную]',
            lease_owner = NULL,
            lease_expires_at = NULL,
            result = COALESCE(result, '{}'::jsonb) || jsonb_build_object(
                'outcome', 'UNKNOWN',
                'reconcile_required', true,
                'reason', 'stuck_campaign_create_after_worker_loss'
            ),
            updated_at = NOW()
        WHERE task_type = 'campaign_create'
          AND status = 'running'
          AND (
              (lease_expires_at IS NOT NULL AND lease_expires_at <= NOW())
              OR (
                  lease_expires_at IS NULL
                  AND updated_at < NOW() - make_interval(secs => :sec)
              )
          )
        RETURNING id, correlation_id, payload, status, result,
                  requested_by, lane, task_type
        """
    )
    async with engine.begin() as conn:
        result = await conn.execute(stmt, {"sec": int(stuck_after_seconds)})
        rows, n = _returned_task_rows(result)
        await _transition_returned_terminal_tasks(conn, rows, force_phase="unknown")
    if n:
        logger.error(
            "reconcile: %d зависших campaign_create → failed без retry "
            "(возможен дубль/осиротевшая кампания в Meta — нужна ручная проверка)",
            n,
        )
    return n


async def reconcile_stuck_running(
    engine: AsyncEngine,
    *,
    stuck_after_seconds: int = 1800,
    exclude_kinds: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> int:
    """Safely reconcile a task whose worker lease expired.

    Делает один bump attempt_count (worker крашнулся ДО вызова requeue_for_retry,
    так что инкремент попыток нужно сделать здесь — иначе бесконечный retry).

    exclude_kinds — meta_api_mutation mutation_kind, которые НЕЛЬЗЯ ретраить.
    Для duplicate_adset_structure это не позволяет общему reconciler обойти
    checkpointed PAUSE-recovery или no-checkpoint UNKNOWN finalizer.

    Необратимые task_type целиком (IRREVERSIBLE_TASK_TYPES, напр. campaign_create)
    ИСКЛЮЧАЮТСЯ ВСЕГДА, безусловно: их зависшие строки уводит в failed
    fail_stuck_campaign_create. retry создания кампании = дубль + двойной открут.

    До внешней границы задача может быть повторена, пока не исчерпаны попытки.
    После внешней границы status-actions получают только read-before-retry, а
    необратимые операции закрываются с ``UNKNOWN``. Каждому допустимому retry
    новый execution deadline выдаётся при следующем claim; старый deadline уже
    истёк и не может быть переиспользован.

    Используется reconciler_worker'ом. Возвращает число обработанных строк.
    Не должно быть продублировано в reconciler_worker — иначе attempt_count
    бампается дважды и max_attempts исчерпывается за вдвое меньше попыток.
    """
    exclude = [k for k in (exclude_kinds or ()) if k]
    irreversible_types = sorted(IRREVERSIBLE_TASK_TYPES)
    params: dict[str, Any] = {"sec": int(stuck_after_seconds), "irrev_types": irreversible_types}
    # Безусловный guard: необратимые task_type целиком вне requeue (money-safety).
    guard = "\n  AND task_type NOT IN :irrev_types"
    if exclude:
        # Не ретраим необратимые Meta mutations: ими владеют специальные finalizers.
        guard += (
            "\n  AND NOT (task_type = 'meta_api_mutation' "
            "AND payload->>'mutation_kind' IN :exclude_kinds)"
        )
        params["exclude_kinds"] = exclude
    stmt = text(
        """
        UPDATE task_queue
        SET status = CASE
                WHEN external_started_at IS NOT NULL
                 AND task_type = 'meta_api_mutation'
                 AND payload->>'mutation_kind' IN
                    ('pause_ad','activate_ad','bulk_status_change')
                    THEN 'retrying'
                WHEN attempt_count + 1 >= max_attempts THEN 'failed'
                WHEN external_started_at IS NOT NULL THEN 'failed'
                ELSE 'retrying'
            END,
            attempt_count = attempt_count + 1,
            available_at = CASE
                WHEN (
                    external_started_at IS NOT NULL
                    AND task_type = 'meta_api_mutation'
                    AND payload->>'mutation_kind' IN
                        ('pause_ad','activate_ad','bulk_status_change')
                )
                OR (
                    external_started_at IS NULL
                    AND attempt_count + 1 < max_attempts
                )
                    THEN NOW()
                ELSE available_at
            END,
            deadline_at = CASE
                WHEN (
                    external_started_at IS NOT NULL
                    AND task_type = 'meta_api_mutation'
                    AND payload->>'mutation_kind' IN
                        ('pause_ad','activate_ad','bulk_status_change')
                )
                OR (
                    external_started_at IS NULL
                    AND attempt_count + 1 < max_attempts
                )
                    THEN CASE
                        WHEN lane = 'money' THEN NULL
                        ELSE NOW() + make_interval(secs => CASE lane
                            WHEN 'bulk' THEN 1800
                            ELSE 120
                        END)
                    END
                ELSE deadline_at
            END,
            completed_at = CASE
                WHEN external_started_at IS NOT NULL
                 AND task_type = 'meta_api_mutation'
                 AND payload->>'mutation_kind' IN
                    ('pause_ad','activate_ad','bulk_status_change')
                    THEN completed_at
                WHEN attempt_count + 1 >= max_attempts THEN NOW()
                WHEN external_started_at IS NOT NULL THEN NOW()
                ELSE completed_at
            END,
            result = CASE
                WHEN external_started_at IS NOT NULL THEN
                    COALESCE(result, '{}'::jsonb) || jsonb_build_object(
                        'outcome', 'UNKNOWN',
                        'reconcile_required', true,
                        'reason', 'worker_lease_expired_after_external_start'
                    )
                WHEN attempt_count + 1 >= max_attempts THEN
                    COALESCE(result, '{}'::jsonb) || jsonb_build_object(
                        'outcome', 'REJECTED',
                        'reason', 'attempts_exhausted_before_external_start'
                    )
                ELSE result
            END,
            last_error = COALESCE(last_error, '')
                || CASE
                    WHEN external_started_at IS NOT NULL
                        THEN ' [lease expired after external start: result unknown]'
                    ELSE ' [stuck timeout reconciled before external start]'
                   END,
            lease_owner = NULL,
            lease_expires_at = NULL,
            updated_at = NOW()
        WHERE status = 'running'
          AND (
              (lease_expires_at IS NOT NULL AND lease_expires_at <= NOW())
              OR (
                  lease_expires_at IS NULL
                  AND updated_at < NOW() - make_interval(secs => :sec)
              )
          )"""
        + guard
        + "\nRETURNING id, correlation_id, payload, status, result, "
        "requested_by, lane, task_type"
    ).bindparams(bindparam("irrev_types", expanding=True))
    if exclude:
        stmt = stmt.bindparams(bindparam("exclude_kinds", expanding=True))
    async with engine.begin() as conn:
        result = await conn.execute(stmt, params)
        rows, count = _returned_task_rows(result)
        await _transition_returned_terminal_tasks(conn, rows)
        return count


async def request_task_cancel(
    engine: AsyncEngine,
    *,
    task_id: int,
    reason: str,
) -> bool:
    """Persist a cooperative cancellation request.

    Pending work becomes terminal immediately.  Running work keeps its lease so
    the owner can propagate cancellation through gRPC/AbortController and then
    reconcile the external outcome instead of racing a replacement worker.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET cancel_requested_at = COALESCE(cancel_requested_at, NOW()),
                    cancel_reason = :reason,
                    status = CASE
                        WHEN status IN ('pending', 'retrying')
                         AND external_started_at IS NULL THEN 'cancelled'
                        ELSE status
                    END,
                    completed_at = CASE
                        WHEN status IN ('pending', 'retrying')
                         AND external_started_at IS NULL THEN NOW()
                        ELSE completed_at
                    END,
                    last_error = CASE
                        WHEN status IN ('pending', 'retrying')
                         AND external_started_at IS NULL THEN :reason
                        ELSE last_error
                    END,
                    result = CASE
                        WHEN status IN ('pending', 'retrying')
                         AND external_started_at IS NULL THEN
                            COALESCE(result, '{}'::jsonb)
                            || jsonb_build_object(
                                'outcome', 'REJECTED',
                                'reason', 'cancel_requested_before_external_call'
                            )
                        ELSE result
                    END,
                    updated_at = NOW()
                WHERE id = :id
                  AND status NOT IN ('succeeded', 'failed', 'cancelled')
                RETURNING id, correlation_id, payload, status, result,
                          requested_by, lane, task_type
                """
            ),
            {"id": int(task_id), "reason": reason[:8000]},
        )
        rows, count = _returned_task_rows(result)
        await _transition_returned_terminal_tasks(conn, rows)
    return count > 0


async def expire_overdue_tasks(engine: AsyncEngine) -> int:
    """Expire queued non-money work without erasing an ambiguous outcome.

    Money deadlines start only when a worker claims an execution attempt. Old
    pre-fix rows may still contain enqueue-time deadlines, so the terminalizer
    must explicitly leave all pending/retrying money work claimable.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'failed',
                    completed_at = NOW(),
                    last_error = CASE
                        WHEN external_started_at IS NOT NULL
                          OR COALESCE(result->>'reconcile_required', 'false') = 'true'
                            THEN 'absolute deadline exceeded while external outcome remained unknown'
                        ELSE 'absolute task deadline exceeded before external call'
                    END,
                    result = COALESCE(result, '{}'::jsonb)
                        || jsonb_build_object(
                            'outcome', CASE
                                WHEN external_started_at IS NOT NULL
                                  OR COALESCE(result->>'reconcile_required', 'false') = 'true'
                                    THEN 'UNKNOWN'
                                ELSE 'REJECTED'
                            END,
                            'reconcile_required', CASE
                                WHEN external_started_at IS NOT NULL
                                  OR COALESCE(result->>'reconcile_required', 'false') = 'true'
                                    THEN true
                                ELSE false
                            END,
                            'reason', 'absolute_deadline_exceeded'
                        ),
                    updated_at = NOW()
                WHERE status IN ('pending', 'retrying')
                  AND lane <> 'money'
                  AND deadline_at IS NOT NULL
                  AND deadline_at <= NOW()
                RETURNING id, correlation_id, payload, status, result,
                          requested_by, lane, task_type
                """
            )
        )
        rows, count = _returned_task_rows(result)
        await _transition_returned_terminal_tasks(conn, rows)
    return count


async def refresh_task_queue_metrics(engine: AsyncEngine) -> None:
    """Refresh bounded-cardinality queue gauges from PostgreSQL."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT lane, status, COUNT(*) AS count
                    FROM task_queue
                    WHERE status IN ('pending', 'retrying', 'running', 'failed')
                    GROUP BY lane, status
                    """
                )
            )
        ).all()
        oldest = (
            await conn.execute(
                text(
                    """
                    SELECT lane,
                           EXTRACT(EPOCH FROM (NOW() - MIN(available_at))) AS age_seconds
                    FROM task_queue
                    WHERE status IN ('pending', 'retrying')
                      AND available_at <= NOW()
                    GROUP BY lane
                    """
                )
            )
        ).all()
    counts = {(str(row.lane), str(row.status)): int(row.count) for row in rows}
    ages = {str(row.lane): max(0.0, float(row.age_seconds or 0.0)) for row in oldest}
    for lane in TASK_LANES:
        for status in ("pending", "retrying", "running", "failed"):
            TASK_QUEUE_DEPTH.labels(lane=lane, status=status).set(counts.get((lane, status), 0))
        TASK_OLDEST_PENDING_AGE.labels(lane=lane).set(ages.get(lane, 0.0))


# ====================== inspect ======================


async def get_task_by_idempotency_key(
    engine: AsyncEngine,
    *,
    idempotency_key: str,
) -> Task | None:
    """Поиск по idempotency_key — для проверки дубликата перед create."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, task_type, status, idempotency_key, payload,
                           attempt_count, max_attempts, requested_by, last_error,
                           created_at, external_started_at, result,
                           lane, priority, available_at, deadline_at, lease_owner,
                           lease_token, lease_expires_at, cancel_requested_at,
                           cancel_reason, correlation_id
                    FROM task_queue WHERE idempotency_key = :k LIMIT 1
                    """
                ),
                {"k": idempotency_key},
            )
        ).first()
    return _row_to_task(row) if row else None
