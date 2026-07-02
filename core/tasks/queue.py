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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Допустимые значения task_type — должны совпадать с CHECK constraint в БД
TASK_TYPES = frozenset(
    {"disable", "enable", "plan_run", "meta_api_mutation", "ad_library_scan", "campaign_create"}
)

# Допустимые статусы — должны совпадать с CHECK constraint
TASK_STATUSES = frozenset(
    {"draft", "pending", "running", "succeeded", "failed", "retrying", "cancelled"}
)

# Retry backoff: 30s, 60s, 120s, 240s, 300s (cap)
_RETRY_BASE_SECONDS = 30
_RETRY_MAX_SECONDS = 300

# Время жизни DRAFT-задачи до автоматической отмены reconciler'ом.
# Переиспользуется в схемах API для вычисления expires_at = created_at + TTL.
DRAFT_TTL_SECONDS: int = 24 * 3600


@dataclass
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
    last_error: str | None = None
    next_retry_at: datetime | None = None
    created_at: datetime | None = None


@dataclass
class TaskClaim:
    """Результат claim_next_task — задача либо есть, либо нет."""

    task: Task | None = None
    queue_empty: bool = True


def _calc_next_retry(attempt: int) -> datetime:
    """Exponential backoff: 30s, 60s, 120s, 240s, 300s+."""
    delay = min(_RETRY_BASE_SECONDS * (2**attempt), _RETRY_MAX_SECONDS)
    return datetime.now(timezone.utc) + timedelta(seconds=delay)


def _row_to_task(row: Any) -> Task:
    """Конвертер sqlalchemy row → Task dataclass."""
    payload = row[4]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return Task(
        id=int(row[0]),
        task_type=str(row[1]),
        status=str(row[2]),
        idempotency_key=str(row[3]),
        payload=payload or {},
        attempt_count=int(row[5] or 0),
        max_attempts=int(row[6] or 5),
        requested_by=str(row[7] or ""),
        last_error=row[8],
        next_retry_at=row[9],
        created_at=row[10] if len(row) > 10 else None,
    )


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
) -> int | None:
    """INSERT new task. Idempotent: если idempotency_key уже есть — None.

    status='draft' для AI-предложений; 'pending' для немедленного исполнения.
    created_by_chat_id — TG chat_id инициатора (нужен для owner ACL у DRAFT-задач).
    Для задач, созданных через MCP/HTTP без TG-контекста, передаём None.
    """
    if task_type not in TASK_TYPES:
        raise ValueError(f"Unknown task_type: {task_type}")
    if status not in TASK_STATUSES:
        raise ValueError(f"Unknown status: {status}")

    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload,
                     attempt_count, max_attempts, requested_by, created_by_chat_id)
                VALUES
                    (:tt, :st, :ik, CAST(:pl AS JSONB), 0, :ma, :rb, :ccid)
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
            },
        )
        row = result.first()
    return int(row[0]) if row else None


# ====================== claim ======================


_CLAIM_SQL = text(
    """
    UPDATE task_queue
    SET status = 'running', updated_at = NOW()
    WHERE id = (
        SELECT id FROM task_queue
        WHERE task_type = :tt
          AND status IN ('pending', 'retrying')
          AND (next_retry_at IS NULL OR next_retry_at <= NOW())
        ORDER BY COALESCE(next_retry_at, created_at), id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, task_type, status, idempotency_key, payload,
              attempt_count, max_attempts, requested_by, last_error,
              next_retry_at, created_at
    """
)


async def claim_next_task(engine: AsyncEngine, *, task_type: str) -> TaskClaim:
    """Атомарный захват одной задачи указанного типа.

    Использует UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED) —
    стандартный безопасный паттерн для concurrent workers.
    Если очередь пуста — queue_empty=True, task=None.
    """
    if task_type not in TASK_TYPES:
        raise ValueError(f"Unknown task_type: {task_type}")
    async with engine.begin() as conn:
        row = (await conn.execute(_CLAIM_SQL, {"tt": task_type})).first()
    if not row:
        return TaskClaim(task=None, queue_empty=True)
    return TaskClaim(task=_row_to_task(row), queue_empty=False)


# ====================== finalize ======================


async def mark_succeeded(
    engine: AsyncEngine,
    *,
    task_id: int,
    result: dict[str, Any] | None = None,
) -> bool:
    """Финальный статус: задача выполнена успешно.

    Returns: True если status был 'running' и переведён в 'succeeded'.
    False — update не применился (status уже не 'running'): обычно это race
    с другим воркером, который уже закрыл задачу после reconciler-таймаута.
    Caller обязан залогировать и пропустить любые побочные эффекты.
    """
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
                """
            ),
            {"id": int(task_id), "res": json.dumps(result or {})},
        )
    return (result_obj.rowcount or 0) > 0


async def mark_failed(
    engine: AsyncEngine,
    *,
    task_id: int,
    error: str,
) -> bool:
    """Финальный статус: задача провалена окончательно (исчерпан max_attempts).

    Если attempts < max_attempts — используй requeue_for_retry, не mark_failed.

    Returns: True если status был 'running' и переведён в 'failed'.
    False — update не применился (status уже не 'running'): race с воркером,
    который успел закрыть задачу. Caller должен залогировать.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'failed',
                    last_error = :err,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE id = :id AND status = 'running'
                """
            ),
            {"id": int(task_id), "err": error[:8000]},
        )
    return (result.rowcount or 0) > 0


async def requeue_for_retry(
    engine: AsyncEngine,
    *,
    task_id: int,
    error: str,
    attempt_count: int,
    max_attempts: int,
) -> bool:
    """Решает: ещё retry или окончательный failed?

    Returns: True если retry поставлен (status='retrying').
    False — либо final failed (mark_failed), либо update не применился
    из-за race с другим воркером, который уже завершил задачу.
    Caller'у достаточно различать «retry vs не-retry», тонкая разница
    «final fail vs noop» уже отражена в БД (status='succeeded' остался).
    """
    new_attempt = attempt_count + 1
    if new_attempt >= max_attempts:
        applied = await mark_failed(engine, task_id=task_id, error=error)
        if not applied:
            logger.warning(
                "requeue_for_retry: task_id=%s mark_failed не применился "
                "(status != running) — гонка с другим воркером, пропускаю",
                task_id,
            )
        return False

    next_at = _calc_next_retry(new_attempt)
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    attempt_count = :n,
                    next_retry_at = :nrr,
                    last_error = :err,
                    updated_at = NOW()
                WHERE id = :id AND status = 'running'
                """
            ),
            {
                "id": int(task_id),
                "n": new_attempt,
                "nrr": next_at,
                "err": error[:8000],
            },
        )
        applied = (result.rowcount or 0) > 0
    if not applied:
        logger.warning(
            "requeue_for_retry: task_id=%s переход в retrying не применился "
            "(status != running) — гонка с другим воркером, пропускаю",
            task_id,
        )
    return applied


# ====================== reconcile (вызывается reconciler_worker'ом) ======================


async def fail_stuck_irreversible(
    engine: AsyncEngine,
    *,
    mutation_kinds: frozenset[str] | set[str] | tuple[str, ...],
    task_type: str = "meta_api_mutation",
    stuck_after_seconds: int = 1800,
) -> int:
    """Зависшие в 'running' НЕОБРАТИМЫЕ mutations → 'failed' (НЕ retry). Money-safety.

    Крэш-путь: worker создал кампанию в Meta (create_campaign/duplicate_campaign),
    но умер (SIGKILL/OOM/деплой) ДО mark_succeeded → задача застряла в 'running'.
    Слепой reconcile перевёл бы её в 'retrying' → повторное создание = ДУБЛЬ
    кампании + двойной открут бюджета. Для необратимых kinds это недопустимо:
    помечаем 'failed' с явным error — оператор проверяет Meta вручную.

    Вызывать ПЕРЕД reconcile_stuck_running (тот же набор передать в exclude_kinds —
    двойная защита: даже при гонке между двумя стейтментами requeue их не тронет).
    Возвращает число помеченных failed (>0 → caller шлёт алерт).
    """
    kinds = [k for k in mutation_kinds if k]
    if not kinds:
        return 0
    stmt = text(
        """
        UPDATE task_queue
        SET status = 'failed',
            completed_at = NOW(),
            last_error = COALESCE(last_error, '')
                || ' [stuck irreversible mutation: возможен коммит в Meta до краша '
                || 'воркера — НЕ ретраим (риск дубля кампании), проверь Meta вручную]',
            updated_at = NOW()
        WHERE task_type = :tt
          AND status = 'running'
          AND updated_at < NOW() - make_interval(secs => :sec)
          AND payload->>'mutation_kind' IN :kinds
        """
    ).bindparams(bindparam("kinds", expanding=True))
    async with engine.begin() as conn:
        result = await conn.execute(
            stmt,
            {"tt": task_type, "sec": int(stuck_after_seconds), "kinds": kinds},
        )
        n = int(result.rowcount or 0)
    if n:
        logger.error(
            "reconcile: %d зависших НЕОБРАТИМЫХ mutation(s) (%s) → failed без retry "
            "(возможен дубль кампании в Meta — нужна ручная проверка)",
            n,
            ", ".join(sorted(kinds)),
        )
    return n


# task_type, которые НЕЛЬЗЯ слепо ретраить при зависании в 'running' — необратимое
# создание объектов в Meta (повтор = дубль кампании + двойной открут бюджета). Их
# зависшие строки уводит в failed fail_stuck_campaign_create (НЕ retrying). Зеркалит
# контракт IRREVERSIBLE_MUTATION_KINDS для meta_api_mutation, но на уровне task_type.
# plan_run (H-3, аудит): исполняется creator_worker'ом через Vision — тоже реальный
# залив FB-кампании, повторное исполнение после zombie-краша = дубль. У plan_run нет
# отдельной fail_stuck_* функции (в отличие от campaign_create) — creator_worker
# закрывает зависшую задачу сам через mark_failed в task_loop при неожиданном
# исключении; здесь же — страховка от слепого auto-retry реконсайлером, если задача
# всё же осталась в 'running' (напр. воркер убит SIGKILL посреди process_one_task).
IRREVERSIBLE_TASK_TYPES: frozenset[str] = frozenset({"campaign_create", "plan_run"})


async def fail_stuck_campaign_create(
    engine: AsyncEngine,
    *,
    stuck_after_seconds: int = 1800,
) -> int:
    """Зависшие в 'running' задачи task_type='campaign_create' → 'failed' (НЕ retry).

    Money-safety, зеркало fail_stuck_irreversible для meta-мутаций, но на уровне
    task_type. Крэш-путь: campaign_creator_worker начал создавать кампанию в Meta
    (POST /campaigns/adsets/...) и умер (SIGKILL/OOM/деплой) ДО mark_succeeded → задача
    застряла в 'running'. Слепой reconcile перевёл бы её в 'retrying' → повторный залив =
    ДУБЛЬ кампании + двойной открут бюджета. Помечаем 'failed' с явным error — оператор
    проверяет Meta вручную (осиротевшие объекты в campaign_run.created_meta_ids, если
    воркер успел их записать; иначе — по кабинету).

    Вызывать ПЕРЕД reconcile_stuck_running (тот безусловно исключает campaign_create —
    двойная защита). Возвращает число помеченных failed (>0 → caller шлёт алерт).
    """
    stmt = text(
        """
        UPDATE task_queue
        SET status = 'failed',
            completed_at = NOW(),
            last_error = COALESCE(last_error, '')
                || ' [stuck campaign_create: воркер мог начать залив в Meta до краша '
                || '— НЕ ретраим (риск дубля кампании), проверь Meta вручную]',
            updated_at = NOW()
        WHERE task_type = 'campaign_create'
          AND status = 'running'
          AND updated_at < NOW() - make_interval(secs => :sec)
        """
    )
    async with engine.begin() as conn:
        result = await conn.execute(stmt, {"sec": int(stuck_after_seconds)})
        n = int(result.rowcount or 0)
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
    """Задачи зависшие в 'running' (worker крашнулся, не успел отметить) → retrying.

    Делает один bump attempt_count (worker крашнулся ДО вызова requeue_for_retry,
    так что инкремент попыток нужно сделать здесь — иначе бесконечный retry).

    exclude_kinds — meta_api_mutation mutation_kind, которые НЕЛЬЗЯ ретраить
    (необратимые create_campaign/duplicate_campaign): они ИСКЛЮЧАЮТСЯ из requeue,
    т.к. их обрабатывает fail_stuck_irreversible (money-safety: retry = дубль).

    Необратимые task_type целиком (IRREVERSIBLE_TASK_TYPES, напр. campaign_create)
    ИСКЛЮЧАЮТСЯ ВСЕГДА, безусловно: их зависшие строки уводит в failed
    fail_stuck_campaign_create. retry создания кампании = дубль + двойной открут.

    Используется reconciler_worker'ом. Возвращает число восстановленных строк.
    Не должно быть продублировано в reconciler_worker — иначе attempt_count
    бампается дважды и max_attempts исчерпывается за вдвое меньше попыток.
    """
    exclude = [k for k in (exclude_kinds or ()) if k]
    irreversible_types = sorted(IRREVERSIBLE_TASK_TYPES)
    params: dict[str, Any] = {"sec": int(stuck_after_seconds), "irrev_types": irreversible_types}
    # Безусловный guard: необратимые task_type целиком вне requeue (money-safety).
    guard = "\n  AND task_type NOT IN :irrev_types"
    if exclude:
        # Не ретраим необратимые meta-мутации (их уводит в failed fail_stuck_irreversible).
        guard += (
            "\n  AND NOT (task_type = 'meta_api_mutation' "
            "AND payload->>'mutation_kind' IN :exclude_kinds)"
        )
        params["exclude_kinds"] = exclude
    stmt = text(
        """
        UPDATE task_queue
        SET status = 'retrying',
            attempt_count = attempt_count + 1,
            next_retry_at = NOW(),
            last_error = COALESCE(last_error, '') || ' [stuck timeout reconciled]',
            updated_at = NOW()
        WHERE status = 'running'
          AND updated_at < NOW() - make_interval(secs => :sec)"""
        + guard
    ).bindparams(bindparam("irrev_types", expanding=True))
    if exclude:
        stmt = stmt.bindparams(bindparam("exclude_kinds", expanding=True))
    async with engine.begin() as conn:
        result = await conn.execute(stmt, params)
        return int(result.rowcount or 0)


async def cancel_stale_drafts(
    engine: AsyncEngine,
    *,
    older_than_seconds: int = 24 * 3600,
) -> int:
    """AI-drafts старше 24h без подтверждения → cancelled."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'cancelled',
                    completed_at = NOW(),
                    last_error = 'draft expired without confirmation',
                    updated_at = NOW()
                WHERE status = 'draft'
                  AND created_at < NOW() - make_interval(secs => :sec)
                """
            ),
            {"sec": int(older_than_seconds)},
        )
        return int(result.rowcount or 0)


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
                           next_retry_at, created_at
                    FROM task_queue WHERE idempotency_key = :k LIMIT 1
                    """
                ),
                {"k": idempotency_key},
            )
        ).first()
    return _row_to_task(row) if row else None
