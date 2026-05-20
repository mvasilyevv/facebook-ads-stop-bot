# -*- coding: utf-8 -*-
"""Creator Worker: поллит PlanRun из БД, исполняет через CreatorService gRPC.

Жизненный цикл одного PlanRun:
1. SELECT FOR UPDATE SKIP LOCKED одного queued плана.
2. Перевод в RUNNING + started_at.
3. Открытие gRPC-стрима CreatorService.RunPlan.
4. Аккумулирование PlanEvent в step_log (батчем сохранять каждые N событий).
5. На checkpoint → REQUIRES_ATTENTION + Telegram.
6. На complete.ok=True → SUCCESS, complete.ok=False → FAILED + Telegram.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from clients.python_grpc.client import BrowserAgentClient
from clients.python_grpc.v1 import creator_pb2
from core.db import get_session_factory
from core.domain import PlanRunStatus
from core.models import Plan, PlanRun
from core.observer.runtime_status import update_worker_heartbeat
from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30
STEP_LOG_FLUSH_EVERY = 5  # сохранять step_log в БД каждые N событий
RUN_PLAN_RPC_TIMEOUT_SECONDS = 60 * 30  # лимит на единичный запуск плана
MAX_ERROR_MESSAGE_LENGTH = 2000


async def _heartbeat_loop(status_ref: list[str], message_ref: list[str | None]) -> None:
    """Фоновая задача: heartbeat creator worker каждые HEARTBEAT_INTERVAL_SECONDS."""
    while True:
        await update_worker_heartbeat(
            "creator",
            status=status_ref[0],
            message=message_ref[0],
        )
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def claim_next_plan_run() -> PlanRun | None:
    """Берёт следующий PlanRun со статусом QUEUED. SELECT FOR UPDATE SKIP LOCKED."""
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(PlanRun)
            .where(PlanRun.status == PlanRunStatus.QUEUED)
            .order_by(PlanRun.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()
        if run is None:
            return None

        run.status = PlanRunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(run)
        return run


async def _load_plan_for_run(run_id) -> Plan | None:
    """Загружает Plan по plan_id из PlanRun (двумя запросами — relationship не объявлен)."""
    factory = get_session_factory()
    async with factory() as session:
        plan_run = await session.scalar(select(PlanRun).where(PlanRun.id == run_id))
        if plan_run is None:
            return None
        return await session.scalar(select(Plan).where(Plan.id == plan_run.plan_id))


async def _append_step_log_entries(run_id, entries: list[dict]) -> None:
    """Дописывает события в step_log и коммитит."""
    if not entries:
        return
    factory = get_session_factory()
    async with factory() as session:
        run = await session.scalar(select(PlanRun).where(PlanRun.id == run_id))
        if run is None:
            return
        # SQLAlchemy не отслеживает мутации JSON, поэтому пересобираем список целиком.
        new_log = list(run.step_log or [])
        new_log.extend(entries)
        run.step_log = new_log
        await session.commit()


async def _finalize_plan_run(
    run_id,
    *,
    status: PlanRunStatus,
    error_message: str | None,
    pending_entries: list[dict],
) -> None:
    """Финализирует PlanRun: дописывает оставшиеся события + ставит статус + finished_at."""
    factory = get_session_factory()
    async with factory() as session:
        run = await session.scalar(select(PlanRun).where(PlanRun.id == run_id))
        if run is None:
            return
        if pending_entries:
            new_log = list(run.step_log or [])
            new_log.extend(pending_entries)
            run.step_log = new_log
        run.status = status
        run.finished_at = datetime.now(UTC)
        if error_message:
            run.error_message = error_message[:MAX_ERROR_MESSAGE_LENGTH]
        await session.commit()


def _event_to_log_entry(event: "creator_pb2.PlanEvent") -> dict | None:
    """Преобразует PlanEvent oneof в JSON-сериализуемый dict для step_log."""
    now_iso = datetime.now(UTC).isoformat()
    if event.HasField("started"):
        e = event.started
        return {
            "event": "step_started",
            "step": e.step,
            "index": e.index,
            "timestamp_ms": e.timestamp_ms,
            "logged_at": now_iso,
        }
    if event.HasField("finished"):
        e = event.finished
        entry: dict = {
            "event": "step_finished",
            "step": e.step,
            "index": e.index,
            "timestamp_ms": e.timestamp_ms,
            "logged_at": now_iso,
        }
        if e.detail_json:
            try:
                entry["detail"] = json.loads(e.detail_json)
            except json.JSONDecodeError:
                entry["detail_raw"] = e.detail_json
        return entry
    if event.HasField("failed"):
        e = event.failed
        return {
            "event": "step_failed",
            "step": e.step,
            "index": e.index,
            "error": e.error,
            "timestamp_ms": e.timestamp_ms,
            "logged_at": now_iso,
        }
    if event.HasField("skipped"):
        e = event.skipped
        return {
            "event": "step_skipped",
            "step": e.step,
            "index": e.index,
            "reason": e.reason,
            "timestamp_ms": e.timestamp_ms,
            "logged_at": now_iso,
        }
    if event.HasField("checkpoint"):
        e = event.checkpoint
        return {
            "event": "checkpoint",
            "url": e.url,
            "detail": e.detail,
            "logged_at": now_iso,
        }
    if event.HasField("complete"):
        e = event.complete
        return {
            "event": "complete",
            "ok": e.ok,
            "error": e.error,
            "total_steps": e.total_steps,
            "duration_ms": e.duration_ms,
            "logged_at": now_iso,
        }
    return None


async def _notify_telegram(
    *,
    token: str,
    chat_id: str,
    text: str,
) -> None:
    """Отправляет уведомление в Telegram. Не падает при ошибках."""
    if not token or not chat_id:
        return
    try:
        client = TelegramBotClient(bot_token=token)
        try:
            await client.send_message(chat_id=chat_id, text=text)
        finally:
            await client.close()
    except Exception:
        logger.exception("Creator worker: не удалось отправить Telegram-уведомление")


async def execute_plan_run(
    grpc_client: BrowserAgentClient,
    run: PlanRun,
    *,
    telegram_token: str = "",
    telegram_chat_id: str = "",
) -> PlanRunStatus:
    """Выполняет PlanRun через gRPC-стрим CreatorService.RunPlan.

    Возвращает финальный статус (SUCCESS / FAILED / REQUIRES_ATTENTION).
    """
    plan = await _load_plan_for_run(run.id)
    if plan is None:
        await _finalize_plan_run(
            run.id,
            status=PlanRunStatus.FAILED,
            error_message=f"Plan {run.plan_id} не найден",
            pending_entries=[],
        )
        return PlanRunStatus.FAILED

    plan_payload = {
        "schema_version": plan.schema_version,
        "steps": plan.steps or [],
    }
    plan_json = json.dumps(plan_payload, ensure_ascii=False)
    variables_json = json.dumps(run.variables or {}, ensure_ascii=False)

    logger.info(
        "Creator worker: запускаю PlanRun id=%s plan=%s steps=%d",
        run.id,
        plan.name,
        len(plan.steps or []),
    )

    pending: list[dict] = []
    checkpoint_detected = False
    checkpoint_url = ""
    final_complete_ok: bool | None = None
    final_complete_error = ""

    try:
        async for event in grpc_client.run_plan(plan_json, variables_json):
            entry = _event_to_log_entry(event)
            if entry is None:
                continue
            pending.append(entry)

            if entry["event"] == "checkpoint":
                checkpoint_detected = True
                checkpoint_url = entry.get("url", "")

            if entry["event"] == "complete":
                final_complete_ok = bool(entry["ok"])
                final_complete_error = entry.get("error") or ""

            if len(pending) >= STEP_LOG_FLUSH_EVERY:
                await _append_step_log_entries(run.id, pending)
                pending = []
    except Exception as exc:
        logger.exception("Creator worker: RPC RunPlan завершился исключением")
        await _finalize_plan_run(
            run.id,
            status=PlanRunStatus.FAILED,
            error_message=f"gRPC RunPlan: {exc}",
            pending_entries=pending,
        )
        await _notify_telegram(
            token=telegram_token,
            chat_id=telegram_chat_id,
            text=f"❌ Creator: PlanRun {run.id} упал с ошибкой gRPC: {exc}",
        )
        return PlanRunStatus.FAILED

    if checkpoint_detected:
        await _finalize_plan_run(
            run.id,
            status=PlanRunStatus.REQUIRES_ATTENTION,
            error_message=f"Обнаружен FB checkpoint: {checkpoint_url}",
            pending_entries=pending,
        )
        await _notify_telegram(
            token=telegram_token,
            chat_id=telegram_chat_id,
            text=(
                f"⚠️ Creator: PlanRun {run.id} приостановлен — FB checkpoint.\nURL: {checkpoint_url}"
            ),
        )
        return PlanRunStatus.REQUIRES_ATTENTION

    if final_complete_ok is True:
        await _finalize_plan_run(
            run.id,
            status=PlanRunStatus.SUCCESS,
            error_message=None,
            pending_entries=pending,
        )
        logger.info("Creator worker: PlanRun %s завершён успешно", run.id)
        return PlanRunStatus.SUCCESS

    error_text = final_complete_error or "PlanRun завершился без события complete"
    await _finalize_plan_run(
        run.id,
        status=PlanRunStatus.FAILED,
        error_message=error_text,
        pending_entries=pending,
    )
    await _notify_telegram(
        token=telegram_token,
        chat_id=telegram_chat_id,
        text=f"❌ Creator: PlanRun {run.id} упал: {error_text}",
    )
    return PlanRunStatus.FAILED


async def creator_worker_loop(
    *,
    poll_interval_seconds: float,
    grpc_client: BrowserAgentClient,
    shutdown_event: asyncio.Event,
    status_ref: list[str],
    message_ref: list[str | None],
    telegram_token: str = "",
    telegram_chat_id: str = "",
) -> None:
    """Основной цикл: пока есть QUEUED — берём и исполняем; пусто — ждём poll."""
    while not shutdown_event.is_set():
        run = await claim_next_plan_run()
        if run is None:
            status_ref[0] = "idle"
            message_ref[0] = None
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=poll_interval_seconds)
                break
            except asyncio.TimeoutError:
                continue

        status_ref[0] = "running"
        message_ref[0] = f"PlanRun {run.id}"
        try:
            await execute_plan_run(
                grpc_client,
                run,
                telegram_token=telegram_token,
                telegram_chat_id=telegram_chat_id,
            )
        except Exception:
            logger.exception(
                "Creator worker: непойманное исключение при исполнении PlanRun %s",
                run.id,
            )
            await _finalize_plan_run(
                run.id,
                status=PlanRunStatus.FAILED,
                error_message="Непойманное исключение в creator_worker_loop",
                pending_entries=[],
            )
