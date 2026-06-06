# -*- coding: utf-8 -*-
"""Движок массового отключения объявлений (money-критично).

Выделен из apps/api/routers/v1/disable_tasks.py, чтобы роутер остался тонким
(<500 строк) и логику можно было тестировать изолированно.

Контракт exactly-once: на каждый fb_ad_id создаётся отдельный
create_mutation_task pause_ad с ДЕТЕРМИНИРОВАННЫМ per-ad ключом
`manual:pause_ad:{fb_ad_id}:{idempotency_token}`. UNIQUE-constraint в task_queue
+ ON CONFLICT DO NOTHING гарантируют, что повтор запроса (двойной submit,
сетевой ретрай, две параллельные сессии) не создаст дубль задачи на объявление.

Каждый ad обрабатывается в собственной транзакции (внутри create_mutation_task) —
частичный откат всего batch невозможен: падение на одном ad не теряет уже
созданные задачи.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.utils.task_serializer import task_row_to_out
from core.meta_api.queue import create_mutation_task
from core.meta_api.schemas import MetaMutationPayload

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BulkDisableOutcome:
    """Результат bulk-отключения в partial-failure формате.

    created — задачи, реально созданные ЭТИМ вызовом (dict в shape TaskQueueRowOut);
    skipped — дубли (ключ уже существовал): повтор token'а или race-проигрыш;
    failed  — ad не найден в fb_ads / неожиданная ошибка.
    """

    created: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {"created": self.created, "skipped": self.skipped, "failed": self.failed}


def bulk_pause_ikey(fb_ad_id: str, idempotency_token: str) -> str:
    """Детерминированный per-ad idempotency_key для bulk-отключения.

    Привязан к КАЖДОМУ ad_id + общему client-токену (НЕ per-batch): при ретрае
    всего запроса каждый ad даёт тот же ключ → exactly-once на объявление, даже
    если предыдущий запрос успел создать только часть задач.
    """
    return f"manual:pause_ad:{fb_ad_id}:{idempotency_token}"


def dedup_preserve_order(fb_ad_ids: list[str]) -> list[str]:
    """Схлопывает дубли и пустые внутри одного запроса, сохраняя порядок.

    Одинаковые fb_ad_id в одном body дали бы один ключ — второй всё равно ушёл бы
    в skipped. Схлопываем заранее, чтобы не делать лишний round-trip к БД.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in fb_ad_ids:
        fid = (raw or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        out.append(fid)
    return out


async def _read_task_out(engine: AsyncEngine, task_id: int) -> dict[str, Any] | None:
    """Читает свежую задачу в shape TaskQueueRowOut (ad_name заполняется caller'ом)."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, task_type, status,
                           payload->>'target_id' AS fb_ad_id,
                           NULL::text AS ad_name,
                           attempt_count, max_attempts, requested_by,
                           created_by_chat_id, created_at, updated_at,
                           next_retry_at, last_error
                    FROM task_queue WHERE id = :tid
                    """
                ),
                {"tid": task_id},
            )
        ).first()
    return task_row_to_out(row) if row is not None else None


async def process_bulk_disable(
    engine: AsyncEngine,
    *,
    fb_ad_ids: list[str],
    idempotency_token: str,
    requested_by: str,
    requested_by_chat_id: int | None,
) -> BulkDisableOutcome:
    """Создаёт pause_ad-задачу на каждый уникальный fb_ad_id (idempotent, per-ad).

    Caller отвечает за валидацию входа (cap размера, непустой список) ДО вызова.
    """
    requested_by = (requested_by or "api_user")[:64]  # task_queue.requested_by = String(64)
    outcome = BulkDisableOutcome()

    for fb_ad_id in dedup_preserve_order(fb_ad_ids):
        try:
            async with engine.connect() as conn:
                ad_row = (
                    await conn.execute(
                        text("SELECT id, ad_name FROM fb_ads WHERE fb_ad_id = :fid LIMIT 1"),
                        {"fid": fb_ad_id},
                    )
                ).first()
            if ad_row is None:
                outcome.failed.append({"fb_ad_id": fb_ad_id, "reason": "not_found_in_fb_ads"})
                continue

            ikey = bulk_pause_ikey(fb_ad_id, idempotency_token)
            task_id = await create_mutation_task(
                engine,
                payload=MetaMutationPayload(
                    mutation_kind="pause_ad",
                    target_id=fb_ad_id,
                    params={},
                    ad_account_id=None,
                ),
                requested_by=requested_by,
                status="pending",
                idempotency_key=ikey,
                created_by_chat_id=requested_by_chat_id,
            )

            if task_id is None:
                # ON CONFLICT DO NOTHING → ключ уже есть (повтор/race). Подтягиваем id.
                async with engine.connect() as conn:
                    existing = (
                        await conn.execute(
                            text("SELECT id FROM task_queue WHERE idempotency_key = :k LIMIT 1"),
                            {"k": ikey},
                        )
                    ).first()
                outcome.skipped.append(
                    {
                        "fb_ad_id": fb_ad_id,
                        "task_id": str(existing.id) if existing else None,
                        "reason": "duplicate",
                    }
                )
                continue

            item = await _read_task_out(engine, task_id)
            if item is None:
                outcome.failed.append({"fb_ad_id": fb_ad_id, "reason": "created_but_not_found"})
                continue
            item["ad_name"] = ad_row.ad_name
            outcome.created.append(item)
        except Exception:  # noqa: BLE001 — изолируем сбой одного ad, не валим весь batch
            logger.exception("bulk disable: ошибка при обработке fb_ad_id=%s", fb_ad_id)
            outcome.failed.append({"fb_ad_id": fb_ad_id, "reason": "internal_error"})

    logger.info(
        "bulk disable: by=%s token=%s created=%d skipped=%d failed=%d",
        requested_by,
        idempotency_token,
        len(outcome.created),
        len(outcome.skipped),
        len(outcome.failed),
    )
    return outcome


__all__ = [
    "BulkDisableOutcome",
    "bulk_pause_ikey",
    "dedup_preserve_order",
    "process_bulk_disable",
]
