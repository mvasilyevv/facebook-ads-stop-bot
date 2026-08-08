# -*- coding: utf-8 -*-
"""Cabinet scheduler — раз в минуту проверяет окно автостарта кабинета.

Money-критичный воркер. В плановое время (UTC, ежедневно) автоматически:
1. Включает (enable) объявления СВОИХ кампаний (owner-scoped) с нужной ДАТОЙ
   в названии кампании. Создаёт сразу pending-задачу bulk_status_change activate
   (без дополнительного подтверждения — это автостарт).
2. Ставит durable observer_scan в PostgreSQL.

Контракт:
- Окно: HH:MM UTC из конфига и до конца суток UTC (catch-up при downtime воркера).
- Защита от повторов: task_queue idempotency + фактические ad_ids за день.
- Owner-scoping ОБЯЗАТЕЛЕН (тег из observer_config). Без owner-тега фильтр
  выключен (включаются все кампании с датой) — это осознанный режим владельца.
- Пустой список дат → ничего не включаем (безопасность: НЕ включать весь кабинет).
- Process liveness is exported through the worker Prometheus endpoint.

Конфиг (system_config key='cabinet_autostart') меняется без рестарта — читается
на каждом тике.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from core.db import WORKER_ENGINE_KWARGS
from core.meta_api.bulk import (
    MAX_BULK,
    capture_autostart_activation_guards,
    resolve_owner_ads_by_account,
)
from core.meta_api.queue import create_mutation_task
from core.meta_api.schemas import MetaMutationPayload
from core.observer.queries import load_observer_config, load_scanning_enabled
from core.observer.scan_tasks import enqueue_observer_scan, observer_scan_idempotency_key
from core.scheduler.cabinet_autostart import (
    is_in_autostart_window,
    read_autostart_config,
)
from core.telegram.worker_notify import notify_owners_in_transaction
from core.worker_metrics import mark_worker_heartbeat

logger = logging.getLogger("cabinet_scheduler")

WORKER_NAME = "cabinet_scheduler"
_METRICS_INTERVAL_SECONDS = 15.0

# Главный цикл — раз в минуту (как digest_scheduler / health_watchdog).
CHECK_INTERVAL_SECONDS = int(os.environ.get("CABINET_CHECK_INTERVAL_SEC", "60"))

# Действие mutation — включение (activate). Автостарт всегда включает.
_AUTOSTART_ACTION = "activate"
_AUTOSTART_DAY_LOCK_NAMESPACE = 1128353356

# Потолок резолва объявлений автостарта (защита от резолва всего кабинета). Один
# bulk_status_change ограничен MAX_BULK (лимит Meta Batch API), поэтому всё, что
# выше, режем на чанки по MAX_BULK и создаём отдельную задачу на каждый чанк
# (M3: раньше включались только первые 50, остальные молча отбрасывались).
_AUTOSTART_MAX_ADS = MAX_BULK * 40

# Окно свежести объявления для автостарта (часы). fb_ads.is_active монотонно-истинный
# (выставляется в TRUE и нигде не сбрасывается), поэтому сам по себе НЕ отличает живые
# объявления от давно снятых. Автостарт поднимает только объявления, виденные сканом за
# последние FRESHNESS_HOURS — иначе каждое утро bulk-активировались бы ВСЕ когда-либо
# отсканированные ады (включая прошлые cabinet-дни) → нецелевой открут бюджета. 48ч
# щедро покрывают вчерашний cabinet-день даже при разовом пропуске скана.
AUTOSTART_FRESHNESS_HOURS = int(os.environ.get("CABINET_AUTOSTART_FRESHNESS_HOURS", "48"))


# ====================== one tick ======================


async def run_one_tick(
    *,
    engine: AsyncEngine,
    now: datetime,
) -> dict[str, Any]:
    """Один проход автостарта. Возвращает summary dict с ключом 'outcome'.

    outcome ∈ {'scanning_paused', 'disabled', 'not_in_window', 'already_done',
    'no_campaigns', 'no_owner_ads', 'rejected_missing_account',
    'guard_rejected', 'started'}.

    Шаги:
    0. Глобальный стоп: is_scanning_enabled=false → 'scanning_paused' (асимметричный
       стоп — на паузе НИЧЕГО не включаем). Проверяем ПЕРВЫМ и ДО дедуп-ключа: ключ
       не ставится, поэтому после снятия паузы в том же окне (catch-up до конца суток)
       автостарт доработает. Был на паузе всё окно → день пропущен, без сюрпризов.
    1. Читаем конфиг. Выключен → 'disabled'.
    2. Не в окне → 'not_in_window'.
    3. Читаем уже зафиксированные в task_queue ad_ids за этот день.
    4. Создаём activation tasks только для ещё не запланированных ads.
    5. Атомарно ставим idempotent observer_scan в ту же durable queue.
    """
    # Шаг 0 — money-критичный гейт: на паузе сканирования НЕ включаем объявления
    # и НЕ триггерим скан. Без этого автостарт жёг бы бюджет при «выключенном» боте.
    if not await load_scanning_enabled(engine):
        return {"outcome": "scanning_paused"}

    config = await read_autostart_config(engine)
    if not config.get("enabled"):
        return {"outcome": "disabled"}

    hour_utc = int(config.get("hour_utc", 6))
    minute_utc = int(config.get("minute_utc", 0))
    if not is_in_autostart_window(now, hour_utc, minute_utc):
        return {"outcome": "not_in_window"}

    day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")

    # Источник кампаний автостарта = allowlist отслеживаемых (observer_config.campaign_ids).
    # Объединено: что отслеживаем, то и поднимаем по расписанию. Пустой allowlist →
    # автостарт НИЧЕГО не включает (для observer пусто = «сканировать всё», но включить
    # весь кабинет нельзя — money-дыра).
    observer_config = await load_observer_config(engine)
    owner_tag = (observer_config or {}).get("owner_campaign_tag")
    campaign_ids = list((observer_config or {}).get("campaign_ids") or [])
    if not campaign_ids:
        logger.info(
            "cabinet_autostart: фича включена, но отслеживаемых кампаний нет (allowlist пуст) — "
            "пропускаю день %s",
            day,
        )
        return {"outcome": "no_campaigns", "day": day}

    # Фильтр свежести: включаем только объявления, виденные последним сканом кабинета
    # (last_seen_at >= now - FRESHNESS). Защита от реактивации давно снятых ads —
    # is_active=TRUE монотонно-истинный и мёртвые объявления не отсекает.
    since = now.astimezone(timezone.utc) - timedelta(hours=AUTOSTART_FRESHNESS_HOURS)
    resolution = await resolve_owner_ads_by_account(
        engine,
        owner_tag=owner_tag,
        campaign_ids=campaign_ids,
        since=since,
        limit=_AUTOSTART_MAX_ADS,
    )

    if resolution.missing_account_count:
        logger.error(
            "cabinet_autostart: REJECTED — %d owner ads have no explicit ad_account_id",
            resolution.missing_account_count,
        )
        async with engine.begin() as conn:
            await notify_owners_in_transaction(
                conn,
                event_type="cabinet_autostart",
                severity="critical",
                title=f"Автостарт отклонён · {day}",
                summary="У объявлений нет явного кабинета",
                lines=[f"Без ad_account_id: {resolution.missing_account_count}"],
                status="rejected",
                dedupe_key=f"autostart_alert:{day}:rejected_missing_account",
            )
        return {
            "outcome": "rejected_missing_account",
            "day": day,
            "total": resolution.total,
            "missing_account_count": resolution.missing_account_count,
            "task_ids": [],
            "scan_triggered": False,
        }

    if not resolution.ads_by_account:
        logger.info(
            "cabinet_autostart: по кампаниям %s owner-объявлений не нашлось (owner_tag=%s), day=%s",
            campaign_ids,
            owner_tag,
            day,
        )
        async with engine.begin() as conn:
            scan_receipt = await enqueue_observer_scan(
                engine,
                requested_by="cabinet_autostart",
                reason="autostart_no_owner_ads",
                idempotency_key=observer_scan_idempotency_key("autostart-empty", day),
                connection=conn,
            )
            await notify_owners_in_transaction(
                conn,
                event_type="cabinet_autostart",
                severity="warning",
                title=f"Автостарт не выполнен · {day}",
                summary="Owner-объявления не найдены",
                lines=["Проверь даты в названиях кампаний"],
                status="not_started",
                dedupe_key=f"autostart_alert:{day}:no_owner_ads",
            )
        return {
            "outcome": "no_owner_ads",
            "day": day,
            "total": resolution.total,
            "scan_triggered": scan_receipt.created,
            "scan_task_id": scan_receipt.task_id,
        }

    selected_count = sum(len(ids) for ids in resolution.ads_by_account.values())
    truncated = resolution.total > selected_count
    task_ids: list[int] = []
    newly_targeted: list[str] = []
    chunks_count = 0
    all_resolved_ids = sorted(
        ad_id for account_ads in resolution.ads_by_account.values() for ad_id in account_ads
    )
    guard_rejections: dict[str, str] = {}
    async with engine.begin() as conn:
        # Serialize every scheduler instance before reading the durable daily
        # ledger.  Per-ad locks alone are too late: a losing concurrent tick
        # could otherwise keep its stale empty ledger and enqueue an ungated
        # reconciliation scan after the winner committed the bulk child.
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:autostart_day))"),
            {
                "namespace": _AUTOSTART_DAY_LOCK_NAMESPACE,
                "autostart_day": day,
            },
        )
        existing_task_ids, already_targeted = await _load_scheduled_autostart_ads(
            engine,
            day=day,
            connection=conn,
        )
        pending_by_account = {
            account_id: sorted(set(account_ads) - already_targeted)
            for account_id, account_ads in resolution.ads_by_account.items()
        }
        pending_ids = sorted(
            ad_id for account_ads in pending_by_account.values() for ad_id in account_ads
        )
        # Same per-ad mutex as CommandService. A concurrent stop either commits
        # first and blocks this snapshot or commits after the guarded activation
        # lifecycle and therefore wins the final Meta state.
        for ad_id in pending_ids:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:ad_id))"),
                {"ad_id": ad_id},
            )

        guarded_by_account: dict[str, list[tuple[str, dict[str, object]]]] = {}
        for account_id in sorted(pending_by_account):
            guards = await capture_autostart_activation_guards(
                conn,
                ad_ids=pending_by_account[account_id],
                expected_ad_account_id=account_id,
            )
            guard_rejections.update(guards.rejected_by_ad_id)
            guarded_by_account[account_id] = sorted(guards.guards_by_ad_id.items())

        for account_id in sorted(guarded_by_account):
            guarded_ads = guarded_by_account[account_id]
            for offset in range(0, len(guarded_ads), MAX_BULK):
                guarded_chunk = guarded_ads[offset : offset + MAX_BULK]
                chunk = [ad_id for ad_id, _guard in guarded_chunk]
                chunk_digest = hashlib.sha256(",".join(chunk).encode("utf-8")).hexdigest()[:20]
                payload = MetaMutationPayload(
                    mutation_kind="bulk_status_change",
                    target_id=f"autostart:{account_id}:{chunk_digest}",
                    params={
                        "ad_ids": chunk,
                        "action": _AUTOSTART_ACTION,
                        "autostart_day": day,
                        "resolved_from_campaigns": campaign_ids,
                        "activation_guards": {ad_id: guard for ad_id, guard in guarded_chunk},
                    },
                    ad_account_id=account_id,
                )
                task_id = await create_mutation_task(
                    engine,
                    payload=payload,
                    requested_by="cabinet_autostart",
                    status="pending",
                    idempotency_key=(
                        f"autostart:{day}:{_AUTOSTART_ACTION}:{account_id}:{chunk_digest}"
                    ),
                    connection=conn,
                )
                if task_id is not None:
                    task_ids.append(task_id)
                    newly_targeted.extend(chunk)
                chunks_count += 1

        dependency_task_ids = sorted({*existing_task_ids, *task_ids})
        if dependency_task_ids:
            scan_key = observer_scan_idempotency_key(
                "autostart-barrier",
                f"{day}:{','.join(str(task_id) for task_id in dependency_task_ids)}",
            )
        else:
            scan_key = observer_scan_idempotency_key(
                "autostart-guard",
                f"{day}:{','.join(all_resolved_ids)}",
            )
        scan_receipt = await enqueue_observer_scan(
            engine,
            requested_by="cabinet_autostart",
            reason="autostart_activation_reconciliation",
            idempotency_key=scan_key,
            dependency_task_ids=dependency_task_ids,
            connection=conn,
        )
        if task_ids:
            notification_lines = [f"Объявлений: {len(newly_targeted)} · задач: {len(task_ids)}"]
            if truncated:
                notification_lines.append(
                    f"Найдено {resolution.total}: лимит достигнут, проверь остаток"
                )
            if guard_rejections:
                notification_lines.append(f"Не прошли safety-проверку: {len(guard_rejections)}")
            await notify_owners_in_transaction(
                conn,
                event_type="cabinet_autostart",
                severity="warning",
                title=f"Автостарт кабинета · {day}",
                summary="Задачи активации поставлены в очередь",
                lines=notification_lines,
                status="queued",
                dedupe_key=f"autostart_alert:{day}:started",
            )
        elif not existing_task_ids and guard_rejections:
            await notify_owners_in_transaction(
                conn,
                event_type="cabinet_autostart",
                severity="critical",
                title=f"Автостарт отклонён · {day}",
                summary="Состояние объявлений изменилось или небезопасно",
                lines=[f"Не включено: {len(guard_rejections)}"],
                status="rejected",
                dedupe_key=f"autostart_alert:{day}:guard_rejected",
            )
    if not task_ids and not existing_task_ids and guard_rejections:
        return {
            "outcome": "guard_rejected",
            "day": day,
            "task_ids": [],
            "rejected_ads": len(guard_rejections),
            "rejection_reasons": dict(sorted(guard_rejections.items())),
            "scan_triggered": scan_receipt.created,
            "scan_task_id": scan_receipt.task_id,
        }
    if not task_ids:
        return {
            "outcome": "already_done",
            "day": day,
            "task_ids": existing_task_ids,
            "scan_triggered": scan_receipt.created,
            "scan_task_id": scan_receipt.task_id,
        }

    logger.info(
        "cabinet_autostart: queued tasks=%d new_ads=%d total=%d truncated=%s day=%s",
        len(task_ids),
        len(newly_targeted),
        resolution.total,
        truncated,
        day,
    )
    if truncated:
        logger.warning(
            "cabinet_autostart: total=%d exceeded cap=%d; manual review required (day=%s)",
            resolution.total,
            _AUTOSTART_MAX_ADS,
            day,
        )
    return {
        "outcome": "started",
        "day": day,
        "task_ids": task_ids,
        "ad_count": len(newly_targeted),
        "total": resolution.total,
        "accounts": len(resolution.ads_by_account),
        "chunks": chunks_count,
        "truncated": truncated,
        "guard_rejected_ads": len(guard_rejections),
        "guard_rejection_reasons": dict(sorted(guard_rejections.items())),
        "scan_triggered": True,
        "scan_task_id": scan_receipt.task_id,
    }


async def _load_scheduled_autostart_ads(
    engine: AsyncEngine,
    *,
    day: str,
    connection: AsyncConnection | None = None,
) -> tuple[list[int], set[str]]:
    """Return the durable daily activation ledger from task_queue payloads."""

    async def _load(conn: AsyncConnection) -> list[Any]:
        return (
            await conn.execute(
                text(
                    """
                    SELECT id, payload
                    FROM task_queue
                    WHERE task_type = 'meta_api_mutation'
                      AND requested_by = 'cabinet_autostart'
                      AND payload->>'mutation_kind' = 'bulk_status_change'
                      AND payload #>> '{params,action}' = 'activate'
                      AND payload #>> '{params,autostart_day}' = :day
                    ORDER BY id
                    """
                ),
                {"day": day},
            )
        ).all()

    if connection is not None:
        rows = await _load(connection)
    else:
        async with engine.connect() as conn:
            rows = await _load(conn)
    task_ids: list[int] = []
    targeted: set[str] = set()
    for row in rows:
        task_ids.append(int(row.id))
        payload = row.payload if isinstance(row.payload, dict) else {}
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        ad_ids = params.get("ad_ids") if isinstance(params, dict) else []
        if isinstance(ad_ids, list):
            targeted.update(str(ad_id) for ad_id in ad_ids if ad_id)
    return task_ids, targeted


# ====================== loops ======================


async def metrics_loop(stop: asyncio.Event) -> None:
    """Refresh the process-local Prometheus liveness gauge."""
    while not stop.is_set():
        mark_worker_heartbeat(WORKER_NAME)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_METRICS_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def tick_loop(
    *,
    engine: AsyncEngine,
    stop: asyncio.Event,
) -> None:
    """Основной цикл — раз в минуту прогоняет run_one_tick."""
    while not stop.is_set():
        try:
            now = datetime.now(timezone.utc)
            summary = await run_one_tick(engine=engine, now=now)
            outcome = summary.get("outcome")
            if outcome not in ("scanning_paused", "disabled", "not_in_window", "already_done"):
                logger.info("cabinet_autostart tick: %s", summary)
        except Exception:
            logger.exception("Ошибка в cabinet_autostart tick")
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


# ====================== entrypoint ======================


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


async def main_loop(database_url: str | None = None) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("cabinet_scheduler запущен (tick=%ss)", CHECK_INTERVAL_SECONDS)
    try:
        await asyncio.gather(
            metrics_loop(stop),
            tick_loop(engine=engine, stop=stop),
        )
    finally:
        await engine.dispose()
        logger.info("cabinet_scheduler остановлен")


__all__ = [
    "WORKER_NAME",
    "main_loop",
    "metrics_loop",
    "run_one_tick",
    "tick_loop",
]
