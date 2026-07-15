# -*- coding: utf-8 -*-
"""Логика одного прогона reconciler-воркера.

Что делает:
1. Переводит task_queue.status='running' AND updated_at < now() - 30min → retrying
   (с инкрементом attempt_count: worker крашнулся ДО вызова requeue_for_retry,
   так что инкремент попыток делается внутри reconcile_stuck_running канона).
2. Auto-cancel черновики старше 24h (страховка от cleanup_worker — он раз в сутки).

Реальная SQL-логика живёт в `core.tasks.queue` — здесь только параметры из env и
orchestration. Раньше эти функции дублировались тут, в результате attempt_count
бампался дважды (worker.py +1 и потом requeue_for_retry +1 после нового claim).
"""

from __future__ import annotations

import logging
import os

from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.schemas import IRREVERSIBLE_MUTATION_KINDS
from core.tasks.queue import (
    cancel_stale_drafts as _canonical_cancel_stale_drafts,
)
from core.tasks.queue import (
    fail_stuck_campaign_create as _canonical_fail_stuck_campaign_create,
)
from core.tasks.queue import (
    fail_stuck_duplicate_without_checkpoint as _canonical_fail_stuck_duplicate_without_checkpoint,
)
from core.tasks.queue import (
    fail_stuck_irreversible as _canonical_fail_stuck_irreversible,
)
from core.tasks.queue import (
    fail_stuck_plan_run as _canonical_fail_stuck_plan_run,
)
from core.tasks.queue import (
    prepare_stuck_duplicate_recovery as _canonical_prepare_stuck_duplicate_recovery,
)
from core.tasks.queue import (
    reconcile_stuck_running as _canonical_reconcile_stuck_running,
)

logger = logging.getLogger(__name__)

_STUCK_TIMEOUT_MIN = int(os.environ.get("RECONCILER_STUCK_TIMEOUT_MIN", "30"))
_DRAFT_TIMEOUT_HOURS = int(os.environ.get("RECONCILER_DRAFT_TIMEOUT_HOURS", "24"))
_RECOVERABLE_DUPLICATE_KIND = "duplicate_adset_structure"
_GENERIC_IRREVERSIBLE_KINDS = frozenset(IRREVERSIBLE_MUTATION_KINDS - {_RECOVERABLE_DUPLICATE_KIND})


async def prepare_duplicate_recovery(engine: AsyncEngine) -> int:
    """Stale checkpointed duplicate → PAUSE-only recovery claim."""
    return await _canonical_prepare_stuck_duplicate_recovery(
        engine,
        stuck_after_seconds=_STUCK_TIMEOUT_MIN * 60,
    )


async def fail_duplicate_without_checkpoint(engine: AsyncEngine) -> int:
    """Stale duplicate with no created-ID checkpoint → failed, never replayed."""
    return await _canonical_fail_stuck_duplicate_without_checkpoint(
        engine,
        stuck_after_seconds=_STUCK_TIMEOUT_MIN * 60,
    )


async def fail_irreversible_stuck(engine: AsyncEngine) -> int:
    """Зависшие НЕОБРАТИМЫЕ meta-мутации (create/duplicate) → failed (НЕ retry).

    Money-safety: см. core.tasks.queue.fail_stuck_irreversible. Вызывать ДО
    reconcile_stuck_running. Returns: число помеченных failed.
    """
    stuck_after_seconds = _STUCK_TIMEOUT_MIN * 60
    return await _canonical_fail_stuck_irreversible(
        engine,
        mutation_kinds=_GENERIC_IRREVERSIBLE_KINDS,
        stuck_after_seconds=stuck_after_seconds,
    )


async def fail_stuck_campaign_create(engine: AsyncEngine) -> int:
    """Зависшие задачи campaign_create (необратимый залив) → failed (НЕ retry).

    Money-safety: см. core.tasks.queue.fail_stuck_campaign_create. Вызывать ДО
    reconcile_stuck_running. Returns: число помеченных failed.
    """
    stuck_after_seconds = _STUCK_TIMEOUT_MIN * 60
    return await _canonical_fail_stuck_campaign_create(
        engine, stuck_after_seconds=stuck_after_seconds
    )


async def fail_stuck_plan_run(engine: AsyncEngine) -> int:
    """Зависшие задачи plan_run (необратимый Vision-залив) → failed (НЕ retry).

    Money-safety (аудит 2026-07-12, M-3): см. core.tasks.queue.fail_stuck_plan_run.
    Вызывать ДО reconcile_stuck_running. Returns: число помеченных failed.
    """
    stuck_after_seconds = _STUCK_TIMEOUT_MIN * 60
    return await _canonical_fail_stuck_plan_run(engine, stuck_after_seconds=stuck_after_seconds)


async def reconcile_stuck_running(engine: AsyncEngine) -> int:
    """Обёртка вокруг core.tasks.queue.reconcile_stuck_running с env-таймаутом.

    Необратимые meta-мутации ИСКЛЮЧАЮТСЯ из requeue (их уводит в failed
    fail_irreversible_stuck) — иначе retry создал бы дубль кампании.

    Returns: количество переведённых задач.
    """
    stuck_after_seconds = _STUCK_TIMEOUT_MIN * 60
    return await _canonical_reconcile_stuck_running(
        engine,
        stuck_after_seconds=stuck_after_seconds,
        exclude_kinds=IRREVERSIBLE_MUTATION_KINDS,
    )


async def cancel_old_drafts(engine: AsyncEngine) -> int:
    """Обёртка вокруг core.tasks.queue.cancel_stale_drafts с env-таймаутом.

    Returns: количество отменённых draft'ов.
    """
    older_than_seconds = _DRAFT_TIMEOUT_HOURS * 3600
    return await _canonical_cancel_stale_drafts(engine, older_than_seconds=older_than_seconds)


def render_irreversible_alert(count: int) -> str:
    """HTML-текст алерта о зависших необратимых мутациях (pure, для тестов)."""
    return (
        f"🛑 <b>Reconciler</b>\n"
        f"Зависших необратимых мутаций (create/duplicate): <b>{count}</b> — "
        f"помечены failed без retry.\n"
        f"Воркер мог упасть ПОСЛЕ коммита в Meta — <b>проверь кабинет вручную</b> "
        f"на дубли кампаний."
    )


def render_campaign_create_alert(count: int) -> str:
    """HTML-текст алерта о зависших задачах создания кампаний (pure, для тестов)."""
    return (
        f"🛑 <b>Reconciler</b>\n"
        f"Зависших заливов кампаний (campaign_create): <b>{count}</b> — "
        f"помечены failed без retry.\n"
        f"campaign_creator_worker мог упасть ПОСЛЕ начала залива в Meta — "
        f"<b>проверь кабинет вручную</b> на дубли/осиротевшие кампании "
        f"(см. campaign_run.created_meta_ids)."
    )


def render_duplicate_recovery_alert(count: int) -> str:
    """Immediate owner warning: Meta worker may still be unavailable."""
    return (
        f"🛑 <b>Reconciler: crash дублирования адсетов</b>\n"
        f"Задач с checkpoint: <b>{count}</b>. Поставлен PAUSE-only recovery без "
        f"повторного создания.\n"
        f"До завершения recovery <b>проверь созданные объекты вручную в Ads Manager</b>."
    )


async def _maybe_alert_duplicate_recovery(engine: AsyncEngine, count: int) -> None:
    """Best-effort immediate warning when PAUSE recovery is scheduled."""
    if count <= 0:
        return
    try:
        from core.telegram.worker_notify import notify_recipients

        await notify_recipients(
            engine,
            None,
            category="reconciler_duplicate_recovery",
            text=render_duplicate_recovery_alert(count),
            # Scheduling is itself idempotent per stale running claim. The key is
            # retained for transports that later provide Redis to this worker.
            dedup_key="reconciler:duplicate_adset_structure:recovery_scheduled",
            dedup_ttl_seconds=30 * 60,
        )
    except Exception:  # noqa: BLE001
        logger.exception("reconciler: duplicate recovery alert failed")


async def _maybe_alert_irreversible(engine: AsyncEngine, count: int) -> None:
    """Best-effort TG-алерт о failed необратимых — рассылка всем активным recipients."""
    if count <= 0:
        return
    try:
        from core.telegram.worker_notify import notify_recipients

        await notify_recipients(
            engine,
            None,  # redis не нужен: dedup_key не задан
            category="reconciler_irreversible",
            text=render_irreversible_alert(count),
        )
    except Exception:  # noqa: BLE001
        logger.exception("reconciler: не удалось отправить алерт о необратимых мутациях")


def render_plan_run_alert(count: int) -> str:
    """HTML-текст алерта о зависших Vision-заливах (pure, для тестов)."""
    return (
        f"🛑 <b>Reconciler</b>\n"
        f"Зависших Vision-заливов (plan_run): <b>{count}</b> — "
        f"помечены failed без retry.\n"
        f"creator_worker мог упасть ПОСЛЕ начала исполнения плана — "
        f"<b>проверь кабинет вручную</b> на частично созданные/дублирующие кампании."
    )


async def _maybe_alert_plan_run(engine: AsyncEngine, count: int) -> None:
    """Best-effort TG-алерт о failed зависших plan_run — рассылка recipients."""
    if count <= 0:
        return
    try:
        from core.telegram.worker_notify import notify_recipients

        await notify_recipients(
            engine,
            None,  # redis не нужен: dedup_key не задан
            category="reconciler_plan_run",
            text=render_plan_run_alert(count),
        )
    except Exception:  # noqa: BLE001
        logger.exception("reconciler: не удалось отправить алерт о зависших plan_run")


async def _maybe_alert_campaign_create(engine: AsyncEngine, count: int) -> None:
    """Best-effort TG-алерт о failed зависших campaign_create — рассылка recipients."""
    if count <= 0:
        return
    try:
        from core.telegram.worker_notify import notify_recipients

        await notify_recipients(
            engine,
            None,  # redis не нужен: dedup_key не задан
            category="reconciler_campaign_create",
            text=render_campaign_create_alert(count),
        )
    except Exception:  # noqa: BLE001
        logger.exception("reconciler: не удалось отправить алерт о зависших campaign_create")


async def run_once(engine: AsyncEngine) -> dict[str, int]:
    """Один прогон reconciler. Возвращает counters."""
    counts: dict[str, int] = {}

    # ВАЖЕН ПОРЯДОК: checkpointed duplicate_adset_structure сначала переводим в
    # PAUSE-only recovery. Затем отдельно fail'им duplicate без checkpoint и только
    # после этого — остальные необратимые kinds. Так generic UPDATE никогда не
    # закроет задачу с известными created IDs до cleanup.
    try:
        counts["duplicate_recovery_scheduled"] = await prepare_duplicate_recovery(engine)
    except Exception as exc:
        logger.exception("prepare_duplicate_recovery failed: %s", exc)
        counts["duplicate_recovery_scheduled"] = -1

    try:
        counts["duplicate_without_checkpoint_failed"] = await fail_duplicate_without_checkpoint(
            engine
        )
    except Exception as exc:
        logger.exception("fail_duplicate_without_checkpoint failed: %s", exc)
        counts["duplicate_without_checkpoint_failed"] = -1

    try:
        counts["irreversible_failed"] = await fail_irreversible_stuck(engine)
    except Exception as exc:
        logger.exception("fail_irreversible_stuck failed: %s", exc)
        counts["irreversible_failed"] = -1

    try:
        counts["campaign_create_failed"] = await fail_stuck_campaign_create(engine)
    except Exception as exc:
        logger.exception("fail_stuck_campaign_create failed: %s", exc)
        counts["campaign_create_failed"] = -1

    try:
        counts["plan_run_failed"] = await fail_stuck_plan_run(engine)
    except Exception as exc:
        logger.exception("fail_stuck_plan_run failed: %s", exc)
        counts["plan_run_failed"] = -1

    try:
        counts["stuck_to_retrying"] = await reconcile_stuck_running(engine)
    except Exception as exc:
        logger.exception("reconcile_stuck_running failed: %s", exc)
        counts["stuck_to_retrying"] = -1

    try:
        counts["drafts_cancelled"] = await cancel_old_drafts(engine)
    except Exception as exc:
        logger.exception("cancel_old_drafts failed: %s", exc)
        counts["drafts_cancelled"] = -1

    if counts.get("irreversible_failed", 0) > 0:
        await _maybe_alert_irreversible(engine, counts["irreversible_failed"])

    if counts.get("duplicate_recovery_scheduled", 0) > 0:
        await _maybe_alert_duplicate_recovery(
            engine,
            counts["duplicate_recovery_scheduled"],
        )

    if counts.get("campaign_create_failed", 0) > 0:
        await _maybe_alert_campaign_create(engine, counts["campaign_create_failed"])

    if counts.get("plan_run_failed", 0) > 0:
        await _maybe_alert_plan_run(engine, counts["plan_run_failed"])

    if any(v > 0 for v in counts.values()):
        logger.info("reconciler counts: %s", counts)
    return counts
