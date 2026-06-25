# -*- coding: utf-8 -*-
"""campaign_creator_worker — исполнитель залива FB-кампаний (Подход A, Волна 2).

Поллит task_queue (task_type='campaign_create'), по payload.run_id грузит CampaignRun,
гоняет execute_campaign_spec (uniquify → upload → create) и пишет прогресс/статус/
created_meta_ids обратно в campaign_run. Money-критичный путь: статусы объектов по
launch_state (кампания PAUSED), idempotency_key против двойного залива, partial-create
без retry (дубли недопустимы).

Этот модуль — выделенные хелперы persistence/claim (чтобы main.py остался тонким):
- claim_campaign_task: FOR UPDATE SKIP LOCKED по task_type='campaign_create' (отдельный
  SQL, т.к. core.tasks.queue.claim_next_task валидирует task_type по своему реестру);
- load_run / update_run_progress / finalize_run_* : чтение и запись campaign_run.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.campaign_builder.config import CampaignConfig
from core.campaign_builder.uniquify import ConceptInput
from core.tasks.queue import Task, TaskClaim, _row_to_task

logger = logging.getLogger("campaign_creator_worker")

TASK_TYPE = "campaign_create"


# ====================== claim ======================

# Зеркало core.tasks.queue._CLAIM_SQL, но без валидации task_type по реестру
# (campaign_create в реестре core.tasks.queue.TASK_TYPES отсутствует — он принадлежит
# этому воркеру). Тот же безопасный паттерн UPDATE ... WHERE id=(SELECT FOR UPDATE
# SKIP LOCKED) для concurrent-воркеров.
_CLAIM_CAMPAIGN_SQL = text(
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


async def claim_campaign_task(engine: AsyncEngine) -> TaskClaim:
    """Атомарный захват одной задачи task_type='campaign_create'."""
    async with engine.begin() as conn:
        row = (await conn.execute(_CLAIM_CAMPAIGN_SQL, {"tt": TASK_TYPE})).first()
    if not row:
        return TaskClaim(task=None, queue_empty=True)
    return TaskClaim(task=_row_to_task(row), queue_empty=False)


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
    params: dict[str, Any] = {"rid": run_id, "st": status}
    set_clause = "status = :st, updated_at = NOW()"
    if progress is not None:
        set_clause += ", progress = CAST(:pr AS JSONB)"
        params["pr"] = json.dumps(progress)
    where = "id = :rid"
    if expect is not None:
        where += " AND status = :expect"
        params["expect"] = expect
    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"UPDATE campaign_run SET {set_clause} WHERE {where}"), params
        )
        return (result.rowcount or 0) > 0


async def update_run_progress(engine: AsyncEngine, run_id: str, progress: dict[str, Any]) -> None:
    """Пишет инкрементальный progress (jsonb) campaign_run."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE campaign_run SET progress = CAST(:pr AS JSONB), updated_at = NOW() "
                "WHERE id = :rid"
            ),
            {"rid": run_id, "pr": json.dumps(progress)},
        )


async def finalize_run_succeeded(
    engine: AsyncEngine,
    run_id: str,
    *,
    created_meta_ids: dict[str, Any],
    progress: dict[str, Any],
) -> None:
    """Финал: status=succeeded + created_meta_ids + последний progress."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE campaign_run
                SET status = 'succeeded',
                    created_meta_ids = CAST(:cmi AS JSONB),
                    progress = CAST(:pr AS JSONB),
                    error = NULL,
                    updated_at = NOW()
                WHERE id = :rid
                """
            ),
            {
                "rid": run_id,
                "cmi": json.dumps(created_meta_ids),
                "pr": json.dumps(progress),
            },
        )


async def finalize_run_failed(
    engine: AsyncEngine,
    run_id: str,
    *,
    error: str,
    created_meta_ids: dict[str, Any] | None = None,
) -> None:
    """Финал: status=failed + текст ошибки (+ опц. осиротевшие created_meta_ids).

    created_meta_ids пишется при partial-create — для ручной чистки осиротевших
    объектов через cleanup-эндпоинт.
    """
    params: dict[str, Any] = {"rid": run_id, "err": error[:8000]}
    set_clause = "status = 'failed', error = :err, updated_at = NOW()"
    if created_meta_ids is not None:
        set_clause += ", created_meta_ids = CAST(:cmi AS JSONB)"
        params["cmi"] = json.dumps(created_meta_ids)
    async with engine.begin() as conn:
        await conn.execute(text(f"UPDATE campaign_run SET {set_clause} WHERE id = :rid"), params)


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
    """media-папка прогона из `cfg.creo_root`.

    creo_root от фронта = bare `upload_id` (из upload-ответа) → резолвим как
    `{CAMPAIGN_UPLOAD_ROOT}/{upload_id}` (туда upload кладёт файлы). Абсолютный путь
    (legacy/тесты) берём как есть. Единый layout: файл по ref = `{creo_dir}/{ref}`.
    """
    raw = os.path.expanduser(creo_root or "")
    path = Path(raw)
    if path.is_absolute():
        return path
    return _campaign_upload_root() / raw


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
    "finalize_run_failed",
    "finalize_run_succeeded",
    "load_run",
    "parse_run_config",
    "set_run_status",
    "update_run_progress",
]
