# -*- coding: utf-8 -*-
"""campaign_creator_worker — исполнитель залива FB-кампаний (Подход A, Волна 2).

Поллит task_queue (task_type='campaign_create'), по payload.run_id грузит CampaignRun,
гоняет execute_campaign_spec (uniquify → upload → create) и пишет прогресс/статус/
created_meta_ids обратно в campaign_run. Money-критичный путь: статусы объектов по
all-paused creation policy, idempotency_key против двойного залива, partial-create
без retry (дубли недопустимы).

Этот модуль — выделенные хелперы persistence/claim (чтобы main.py остался тонким):
- claim_campaign_task: canonical fenced claim для bulk lane;
- load_run / update_run_progress / finalize_run_* : чтение и запись campaign_run.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.campaign_builder.config import CampaignConfig
from core.campaign_builder.uniquify import ConceptInput
from core.tasks.queue import (
    Task,
    TaskClaim,
    claim_browser_ready_task,
    transition_correlated_incident_in_transaction,
    transition_terminal_task_in_transaction,
)

logger = logging.getLogger("campaign_creator_worker")

TASK_TYPE = "campaign_create"


async def claim_campaign_task(engine: AsyncEngine) -> TaskClaim:
    """Claim one bulk campaign only with fresh canonical browser evidence."""
    return await claim_browser_ready_task(
        engine,
        task_type=TASK_TYPE,
        lanes=("bulk",),
    )


# ====================== campaign_run persistence ======================


@dataclass
class LoadedRun:
    """Снимок campaign_run для воркера."""

    id: str
    config: dict[str, Any]
    status: str


async def load_run(engine: AsyncEngine, run_id: str) -> LoadedRun | None:
    """Грузит campaign_run по id (config + текущий статус)."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id, config, status FROM campaign_run WHERE id = :rid"),
                {"rid": run_id},
            )
        ).first()
    if not row:
        return None
    config = row[1]
    if isinstance(config, str):
        config = json.loads(config)
    return LoadedRun(id=str(row[0]), config=config or {}, status=str(row[2]))


def parse_run_config(config: dict[str, Any]) -> CampaignConfig:
    """Десериализует снимок конфига run в CampaignConfig (валидация pydantic)."""
    return CampaignConfig.model_validate(config)


async def set_run_status(
    engine: AsyncEngine,
    run_id: str,
    status: str,
    *,
    task: Task,
    progress: dict[str, Any] | None = None,
    expect: str | None = None,
) -> bool:
    """Обновляет status (+ опц. progress) campaign_run. Идемпотентно по run_id.

    expect — если задан, переход АТОМАРЕН: применяется только если текущий status == expect
    (WHERE status = :expect). Возвращает True если строка обновлена; False если нет
    (статус уже другой — напр. cancelled конкурентной отменой). Используется для перехода
    queued→uniquifying: защита cancel-гонки (cancel успел → переход не пройдёт, воркер прервётся
    ДО создания объектов в Meta). Без expect — безусловный UPDATE, возвращает True.
    """
    params: dict[str, Any] = {
        "rid": run_id,
        "st": status,
        "task_id": task.id,
        "lease_owner": task.lease_owner,
        "lease_token": task.lease_token,
    }
    set_clause = "status = :st, updated_at = NOW()"
    if progress is not None:
        set_clause += ", progress = CAST(:pr AS JSONB)"
        params["pr"] = json.dumps(progress)
    where = """
        id = :rid
        AND EXISTS (
            SELECT 1 FROM task_queue tq
            WHERE tq.id = :task_id AND tq.status = 'running'
              AND tq.lease_owner = :lease_owner AND tq.lease_token = :lease_token
              AND tq.lease_expires_at > clock_timestamp()
              AND tq.cancel_requested_at IS NULL
              AND (tq.deadline_at IS NULL OR tq.deadline_at > NOW())
              AND tq.payload->>'run_id' = CAST(:rid AS TEXT)
        )
    """
    if expect is not None:
        where += " AND status = :expect"
        params["expect"] = expect
    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"UPDATE campaign_run SET {set_clause} WHERE {where}"), params
        )
        return (result.rowcount or 0) > 0


async def update_run_progress(
    engine: AsyncEngine,
    run_id: str,
    progress: dict[str, Any],
    *,
    task: Task,
) -> bool:
    """Write progress only while the exact task lease still owns the run."""
    return await set_run_status(
        engine,
        run_id,
        str(progress.get("stage") or "creating"),
        task=task,
        progress=progress,
    )


class _CampaignFinalizeFenceLost(RuntimeError):
    """Internal rollback signal for a two-row terminal transition."""


def _task_lifecycle_values(row: Any) -> tuple[uuid.UUID | None, dict[str, Any], dict[str, Any]]:
    """Normalize the authoritative task row returned by a terminal UPDATE."""
    raw_correlation_id = getattr(row, "correlation_id", None)
    correlation_id = uuid.UUID(str(raw_correlation_id)) if raw_correlation_id is not None else None
    payload = getattr(row, "payload", None)
    if isinstance(payload, str):
        payload = json.loads(payload)
    result = getattr(row, "result", None)
    if isinstance(result, str):
        result = json.loads(result)
    return correlation_id, dict(payload or {}), dict(result or {})


async def finalize_run_succeeded(
    engine: AsyncEngine,
    run_id: str,
    *,
    task: Task,
    created_meta_ids: dict[str, Any],
    progress: dict[str, Any],
) -> bool:
    """Atomically finalize campaign_run and its fenced task as CONFIRMED."""
    try:
        async with engine.begin() as conn:
            run_result = await conn.execute(
                text(
                    """
                UPDATE campaign_run
                SET status = 'succeeded',
                    created_meta_ids = CAST(:cmi AS JSONB),
                    progress = CAST(:pr AS JSONB),
                    error = NULL,
                    updated_at = NOW()
                WHERE id = :rid
                  AND EXISTS (
                      SELECT 1 FROM task_queue tq
                      WHERE tq.id = :task_id AND tq.status = 'running'
                        AND tq.lease_owner = :lease_owner
                        AND tq.lease_token = :lease_token
                        AND tq.lease_expires_at > clock_timestamp()
                        AND tq.payload->>'run_id' = CAST(:rid AS TEXT)
                  )
                """
                ),
                {
                    "rid": run_id,
                    "cmi": json.dumps(created_meta_ids),
                    "pr": json.dumps(progress),
                    "task_id": task.id,
                    "lease_owner": task.lease_owner,
                    "lease_token": task.lease_token,
                },
            )
            if (run_result.rowcount or 0) <= 0:
                raise _CampaignFinalizeFenceLost
            task_result = await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'succeeded',
                        result = CAST(:result AS JSONB),
                        completed_at = NOW(), last_error = NULL, updated_at = NOW()
                    WHERE id = :task_id AND status = 'running'
                      AND lease_owner = :lease_owner AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                    RETURNING correlation_id, payload, result
                    """
                ),
                {
                    "task_id": task.id,
                    "lease_owner": task.lease_owner,
                    "lease_token": task.lease_token,
                    "result": json.dumps(
                        {
                            "outcome": "CONFIRMED",
                            "run_id": run_id,
                            **created_meta_ids,
                        }
                    ),
                },
            )
            task_row = task_result.first()
            if task_row is None:
                raise _CampaignFinalizeFenceLost
            correlation_id, payload, _ = _task_lifecycle_values(task_row)
            await transition_correlated_incident_in_transaction(
                conn,
                task_id=task.id,
                correlation_id=correlation_id,
                phase="confirmed",
                payload=payload,
            )
    except _CampaignFinalizeFenceLost:
        return False
    return True


async def finalize_run_failed(
    engine: AsyncEngine,
    run_id: str,
    *,
    task: Task,
    error: str,
    created_meta_ids: dict[str, Any] | None = None,
    task_result: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
) -> bool:
    """Финал: status=failed + текст ошибки (+ опц. осиротевшие created_meta_ids).

    created_meta_ids пишется при partial-create для fenced reconciliation и
    обязательной ручной сверки до повторного запуска.
    """
    params: dict[str, Any] = {
        "rid": run_id,
        "err": error[:8000],
        "task_id": task.id,
        "lease_owner": task.lease_owner,
        "lease_token": task.lease_token,
    }
    set_clause = "status = 'failed', error = :err, updated_at = NOW()"
    if created_meta_ids is not None:
        set_clause += ", created_meta_ids = CAST(:cmi AS JSONB)"
        params["cmi"] = json.dumps(created_meta_ids)
    if progress is not None:
        set_clause += ", progress = CAST(:progress AS JSONB)"
        params["progress"] = json.dumps(progress)
    result_payload = dict(task_result or {"outcome": "REJECTED"})
    if created_meta_ids is not None:
        result_payload.setdefault("created_ids", created_meta_ids)
    try:
        async with engine.begin() as conn:
            run_result = await conn.execute(
                text(
                    f"""
                    UPDATE campaign_run SET {set_clause}
                    WHERE id = :rid
                      AND EXISTS (
                          SELECT 1 FROM task_queue tq
                          WHERE tq.id = :task_id AND tq.status = 'running'
                            AND tq.lease_owner = :lease_owner
                            AND tq.lease_token = :lease_token
                            AND tq.lease_expires_at > clock_timestamp()
                            AND tq.payload->>'run_id' = CAST(:rid AS TEXT)
                      )
                    """
                ),
                params,
            )
            if (run_result.rowcount or 0) <= 0:
                raise _CampaignFinalizeFenceLost
            queue_result = await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'failed', last_error = :err,
                        result = CAST(:task_result AS JSONB),
                        completed_at = NOW(), updated_at = NOW()
                    WHERE id = :task_id AND status = 'running'
                      AND lease_owner = :lease_owner AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                    RETURNING correlation_id, payload, result
                    """
                ),
                {
                    **params,
                    "task_result": json.dumps(result_payload),
                },
            )
            task_row = queue_result.first()
            if task_row is None:
                raise _CampaignFinalizeFenceLost
            correlation_id, payload, stored_result = _task_lifecycle_values(task_row)
            phase = (
                "unknown"
                if stored_result.get("outcome") == "UNKNOWN"
                or stored_result.get("reconcile_required") is True
                else "failed"
            )
            await transition_terminal_task_in_transaction(
                conn,
                task_id=task.id,
                correlation_id=correlation_id,
                phase=phase,
                payload=payload,
                result=stored_result,
                requested_by=task.requested_by,
                lane=task.lane,
                task_type=task.task_type,
            )
    except _CampaignFinalizeFenceLost:
        return False
    return True


async def finalize_run_cancelled(
    engine: AsyncEngine,
    run_id: str,
    *,
    task: Task,
    reason: str,
) -> bool:
    """Atomically cancel a run and task before the first external boundary."""
    result_payload = {
        "outcome": "REJECTED",
        "operation": "campaign_create",
        "run_id": run_id,
        "reason": "cancel_requested_before_external_call",
    }
    try:
        async with engine.begin() as conn:
            run_result = await conn.execute(
                text(
                    """
                    UPDATE campaign_run
                    SET status = 'cancelled', error = :reason,
                        progress = jsonb_build_object(
                            'stage', 'cancelled', 'outcome', 'REJECTED'
                        ),
                        updated_at = NOW()
                    WHERE id = :rid
                      AND EXISTS (
                          SELECT 1 FROM task_queue tq
                          WHERE tq.id = :task_id AND tq.status = 'running'
                            AND tq.external_started_at IS NULL
                            AND tq.lease_owner = :lease_owner
                            AND tq.lease_token = :lease_token
                            AND tq.lease_expires_at > clock_timestamp()
                            AND tq.payload->>'run_id' = CAST(:rid AS TEXT)
                      )
                    """
                ),
                {
                    "rid": run_id,
                    "task_id": task.id,
                    "lease_owner": task.lease_owner,
                    "lease_token": task.lease_token,
                    "reason": reason[:8000],
                },
            )
            if (run_result.rowcount or 0) <= 0:
                raise _CampaignFinalizeFenceLost
            task_result = await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'cancelled', completed_at = NOW(),
                        last_error = :reason, result = CAST(:result AS JSONB),
                        updated_at = NOW()
                    WHERE id = :task_id AND status = 'running'
                      AND external_started_at IS NULL
                      AND lease_owner = :lease_owner AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                    RETURNING correlation_id, payload, result
                    """
                ),
                {
                    "task_id": task.id,
                    "lease_owner": task.lease_owner,
                    "lease_token": task.lease_token,
                    "reason": reason[:8000],
                    "result": json.dumps(result_payload),
                },
            )
            task_row = task_result.first()
            if task_row is None:
                raise _CampaignFinalizeFenceLost
            correlation_id, payload, _ = _task_lifecycle_values(task_row)
            await transition_correlated_incident_in_transaction(
                conn,
                task_id=task.id,
                correlation_id=correlation_id,
                phase="cancelled",
                payload=payload,
            )
    except _CampaignFinalizeFenceLost:
        return False
    return True


# ====================== resolve concepts ======================

# Расширения видео — единый источник в core.campaign_builder.config (без дрейфа копий).
from core.campaign_builder.config import VIDEO_EXTS as _VIDEO_EXTS  # noqa: E402


def _campaign_upload_root() -> Path:
    """Корень upload-папок концептов (ЗЕРКАЛО роутера apps/api/.../campaigns_create.py).

    Воркер обязан читать файлы РОВНО там, куда их положил upload-эндпоинт:
    `{CAMPAIGN_UPLOAD_ROOT}/{upload_id}/{ref}`. Env переопределяет дефолт
    (на удалённом хосте — рядом с воркером).
    """
    raw = os.environ.get("CAMPAIGN_UPLOAD_ROOT", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / "Documents" / "FB_Agent_Campaign_Uploads"


def _resolve_creo_dir(creo_root: str) -> Path:
    """Resolve one opaque upload ID inside the campaign media store."""
    upload_id = (creo_root or "").strip()
    if not upload_id or Path(upload_id).name != upload_id or upload_id in {".", ".."}:
        raise ValueError("invalid campaign upload id")
    return _campaign_upload_root() / upload_id


def resolve_concepts_from_config(cfg: CampaignConfig) -> dict[str, list[ConceptInput]]:
    """Резолвит концепты каждого блока по ЕДИНОМУ источнику — block.concept_refs.

    Контракт (money-CRIT preview==залив): фронт назначает на кампанию имена файлов
    (`concept_refs`), upload-эндпоинт кладёт каждый файл по имени в media store
    (`{creo_dir}/{ref}`). Воркер материализует РОВНО эти refs — не glob по папке,
    иначе validate (len concept_refs) и залив расходятся.

    Видео грузим как path (ffmpeg по файлу), фото — как bytes (PIL in-memory).

    Пустой concept_refs → ValueError (нет концептов = невалидный залив).
    Отсутствующий на диске файл → ValueError (ref назначен, но не загружен).
    """
    creo_dir = _resolve_creo_dir(cfg.creo_root or "")
    out: dict[str, list[ConceptInput]] = {}
    for block in cfg.campaigns:
        if not block.concept_refs:
            raise ValueError(
                f"кампания {block.key!r}: пустой concept_refs (нет назначенных концептов)"
            )
        concepts: list[ConceptInput] = []
        for index, ref in enumerate(block.concept_refs):
            # Защита от path traversal: ref — имя файла, не путь (срезаем компоненты).
            safe_ref = Path(ref).name
            path = creo_dir / safe_ref
            if not path.is_file():
                raise ValueError(f"кампания {block.key!r}: концепт {ref!r} не найден в {creo_dir}")
            is_video = path.suffix.lower() in _VIDEO_EXTS
            concepts.append(
                ConceptInput(
                    concept_id=f"{block.key}:{index}:{path.stem}",
                    kind="video" if is_video else "image",
                    content=None if is_video else path.read_bytes(),
                    path=str(path),
                    filename=path.name,
                )
            )
        out[block.key] = concepts
    return out


__all__ = [
    "TASK_TYPE",
    "LoadedRun",
    "Task",
    "claim_campaign_task",
    "resolve_concepts_from_config",
    "finalize_run_cancelled",
    "finalize_run_failed",
    "finalize_run_succeeded",
    "load_run",
    "parse_run_config",
    "set_run_status",
    "update_run_progress",
]
