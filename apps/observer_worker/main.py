# -*- coding: utf-8 -*-
"""Observer worker main loop.

Цикл:
1. load_observer_config + check is_scanning_enabled
2. INSERT в scan_runs → scan_id
3. gate.run_scan_cycle() → ScanResult.rows
4. process_scan_rows(...) → метрики/FSM/outbox
5. UPDATE scan_runs финальным результатом
6. Redis heartbeat
7. Sleep на адаптивный интервал (база = interval_seconds = CALM, режим зависит от
   угрозы цикла: stop→CRITICAL ×0.2, warning→ELEVATED ×0.5, офферные ads→CALM ×1.0,
   пусто→IDLE ×1.5) + jitter ±10%. См. core/observer/adaptive_interval.py.

Gate инжектируется (паттерн как у toggle_workers): в проде это BrowserAgentClient,
в тестах — fake который возвращает заранее подготовленные ScannedAdRow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apps.telegram_poller.main import _get_database_url
from core.db import WORKER_ENGINE_KWARGS
from core.observer.accounts import (
    allowlist_blocks_scan,
    list_offers_without_accounts,
    resolve_scan_account_ids,
)
from core.observer.adaptive_interval import (
    JITTER_FRACTION,
    clamp_interval,
    compute_adaptive_interval,
    resolve_scan_mode,
)
from core.observer.pipeline import CycleResult, process_scan_rows
from core.observer.queries import (
    load_observer_config,
    load_vision_auto_restart_flag,
    multi_cabinet_requires_owner_tag,
)
from core.scanner.models import ScannedAdRow
from core.telegram import format as fmt
from core.telegram.worker_notify import notify_owners, notify_recipients

logger = logging.getLogger(__name__)

# Heartbeat — имя ДОЛЖНО совпадать с EXPECTED_WORKERS в health_watchdog.
WORKER_NAME = "observer"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

# TTL ключа observer:runtime. Должен быть с запасом больше интервала скана+jitter,
# иначе ключ протухает между записями: на паузе цикл пишет runtime раз в interval+jitter
# (дефолт 90-105с), и при TTL 60с возникала «дыра» — health_watchdog ложно слал
# «observer:runtime устарел (missing)» каждый час. Держим TTL > watchdog
# OBSERVER_STALE_AFTER_SECONDS (300с), чтобы при реальном зависании срабатывал точный
# staleness-детект по updated_at, а не «missing».
RUNTIME_TTL_SECONDS = int(os.environ.get("OBSERVER_RUNTIME_TTL_SEC", "360"))

# Период освежения observer:runtime во время sleep между сканами. Адаптивный интервал
# (см. core/observer/adaptive_interval.py) в IDLE-режиме при высоком базовом интервале
# может превышать и TTL ключа (360с), и watchdog-порог staleness (300с) — тогда вернулся
# бы ложный «observer:runtime stale/missing»-алерт (тот, что чинили в PR #17). Поэтому
# длинный sleep бьём на чанки ≤ этого значения и между ними переписываем runtime со
# свежим updated_at. Держим заметно < 300с, чтобы updated_at всегда был «молодым».
RUNTIME_REFRESH_SECONDS = 120

# Управляющие каналы observer'а.
CHANNEL_TRIGGER = "fb_agent:observer:trigger"  # форс-скан вне расписания
CHANNEL_CABINET_DAY = "fb_agent:observer:cabinet_day"  # сигнал нового кабинетного дня
CHANNEL_RESTART = "fb_agent:worker:restart:observer"  # graceful restart

# Layer 3 — алерт о «тихой» деградации: observer жив (heartbeat/runtime свежие), но сканы
# стабильно падают и self-heal (Layer 1/2) не помог. Без него мониторинг был слеп ~104 минуты.
DEGRADED_ALERT_THRESHOLD = int(os.environ.get("OBSERVER_DEGRADED_ALERT_THRESHOLD", "3"))
DEGRADED_ALERT_TTL_SECONDS = int(os.environ.get("OBSERVER_DEGRADED_ALERT_TTL_SEC", "1800"))
DEGRADED_ALERT_DEDUP_KEY = "observer:degraded:alerted"

# Money-гард R4: мульти-каб (>1 кабинета) без owner_tag → скан остановлен ради безопасности
# (иначе авто-стоп чужой рекламы в shared-кабинете). Дедуп ops-алерта, чтобы не спамить
# каждый цикл, пока конфиг не исправят.
MULTI_CAB_NO_OWNER_ALERT_TTL_SECONDS = int(
    os.environ.get("OBSERVER_MULTI_CAB_NO_OWNER_ALERT_TTL_SEC", "3600")
)
MULTI_CAB_NO_OWNER_ALERT_DEDUP_KEY = "observer:multi_cab_no_owner:alerted"


@dataclass
class ScanCycleOutput:
    """То что вернул scanner gate за один цикл."""

    rows: list[ScannedAdRow]
    total_passes: int = 0
    duration_seconds: float = 0.0
    empty_reason: str | None = None
    warnings: list[str] | None = None


class ScannerGate(Protocol):
    """Минимальный интерфейс gRPC-клиента observer'а.

    Прод-реализация — обёртка над BrowserAgentClient.run_scan_cycle().
    Тест-реализация — заранее заготовленный список ScannedAdRow.
    """

    async def run_one_scan(
        self,
        campaign_ids: list[str] | None = None,
        owner_tag: str | None = None,
        auto_recover_page: bool = True,
        ad_account_id: str | None = None,
    ) -> ScanCycleOutput:
        """Делает один scan-цикл (am_tabular) и возвращает строки + метаданные.

        campaign_ids — allowlist кампаний (#3). owner_tag — am-резолв campaign.id по тегу
        (тянуть сразу свой скоуп, не весь кабинет). auto_recover_page — self-heal Layer 2:
        при «страница недоступна» эскалировать reconnect (gated vision_config флагом).
        ad_account_id — мульти-кабинет: какой кабинет сканировать (None → legacy
        текущая вкладка). Ошибка сканера → исключение (loop решит retry).
        """
        ...

    async def open_cabinet_tabs(self, ad_account_ids: list[str]) -> list[dict]:
        """Фаза подготовки: открыть вкладки Ads Manager для кабинетов перед сканом.

        Идемпотентно (уже открытая вкладка переиспользуется). Возвращает per-cabinet
        результаты [{ad_account_id, opened, url, error}]. Не бросает на ошибке одного
        кабинета (агрегирует в результат).
        """
        ...


# ====================== Scan_runs writers ======================


async def _begin_scan_run(engine: AsyncEngine, *, ad_account_id: str | None = None) -> int:
    """INSERT в partitioned scan_runs → возвращаем монотонный id.

    Атомарный: scan_id = id за один INSERT через CTE с явным nextval.
    Никакого последующего UPDATE — если процесс крашится до RETURNING, sequence
    откатится вместе с транзакцией и осиротевшего scan_id не возникнет.

    ad_account_id — мульти-кабинет: какой кабинет сканировался (NULL — legacy-скан).
    """
    started_at = datetime.now(timezone.utc)
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    WITH next_id AS (SELECT nextval('scan_runs_id_seq') AS sid)
                    INSERT INTO scan_runs (id, scan_id, started_at, ad_account_id)
                    SELECT sid, sid, :sa, :acct FROM next_id
                    RETURNING id
                    """
                ),
                {"sa": started_at, "acct": ad_account_id},
            )
        ).first()
    return int(row[0])


async def _finish_scan_run(
    engine: AsyncEngine,
    *,
    scan_id: int,
    outcome: str,
    cycle_result: CycleResult | None,
    error_message: str | None = None,
    duration_ms: int = 0,
) -> None:
    """UPDATE финальным результатом цикла."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE scan_runs
                SET finished_at = NOW(),
                    outcome = :oc,
                    rows_total = :rt,
                    alerts_warning = :aw,
                    alerts_stop = :as,
                    error_message = :em,
                    duration_ms = :dm
                WHERE id = :id
                """
            ),
            {
                "id": scan_id,
                "oc": outcome,
                "rt": cycle_result.rows_total if cycle_result else 0,
                "aw": cycle_result.alerts_warning if cycle_result else 0,
                "as": cycle_result.alerts_stop if cycle_result else 0,
                "em": error_message,
                "dm": duration_ms,
            },
        )


# ====================== Redis heartbeat ======================


async def _publish_runtime_status(
    redis_client,
    *,
    status: str,
    active_phase: str | None = None,
    status_message: str | None = None,
    next_scan_at: datetime | None = None,
    last_successful_scan_at: datetime | None = None,
    current_account_id: str | None = None,
    accounts_done: int | None = None,
    accounts_total: int | None = None,
    scan_mode: str | None = None,
) -> None:
    """SET observer:runtime → JSON с TTL RUNTIME_TTL_SECONDS. Frontend/health_watchdog читают ключ.

    Контракт:
        worker_status — детальный статус: "scanning" | "idle" | "dispatch" | "paused"
        status        — нормализованный для читателей: "running" | "paused"
            Маппинг: scanning/idle/dispatch → running, paused → paused

    Мульти-кабинет (аддитивные поля, читатели старого формата не ломаются):
        current_account_id — кабинет, сканируемый прямо сейчас (None вне скана/legacy)
        accounts_done / accounts_total — прогресс обхода кабинетов в цикле

    Читатели используют read_observer_runtime() из core/observer/runtime.py.
    """
    if redis_client is None:
        return

    # Нормализованный статус для читателей (scanning/idle/dispatch/preparing → running)
    _RUNNING_DETAIL = {"scanning", "idle", "dispatch", "preparing"}
    normalized_status = "running" if status in _RUNNING_DETAIL else status

    payload = {
        "worker_status": status,  # детальный (для отладки/granularity)
        "status": normalized_status,  # нормализованный (running|paused) для читателей
        "active_phase": active_phase,
        "status_message": status_message,  # человекочитаемый текст фазы (UI/TG)
        "next_scan_at": next_scan_at.isoformat() if next_scan_at else None,
        "last_successful_scan_at": (
            last_successful_scan_at.isoformat() if last_successful_scan_at else None
        ),
        "current_account_id": current_account_id,
        "accounts_done": accounts_done,
        "accounts_total": accounts_total,
        # Режим адаптивного скана текущего цикла (CRITICAL/ELEVATED/CALM/IDLE) — для UI-индикатора.
        "scan_mode": scan_mode,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await redis_client.set("observer:runtime", json.dumps(payload), ex=RUNTIME_TTL_SECONDS)
    except Exception:
        logger.exception("redis SET observer:runtime failed")


async def _publish_scan_finished(
    redis_client,
    *,
    scan_id: int,
    outcome: str,
    cycle_result: CycleResult | None,
) -> None:
    """PUBLISH fb_agent:scan:finished — trigger refetch на фронте."""
    if redis_client is None:
        return
    event = {
        "scan_id": scan_id,
        "outcome": outcome,
        "rows_total": cycle_result.rows_total if cycle_result else 0,
        "alerts_warning": cycle_result.alerts_warning if cycle_result else 0,
        "alerts_stop": cycle_result.alerts_stop if cycle_result else 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await redis_client.publish("fb_agent:scan:finished", json.dumps(event))
    except Exception:
        logger.exception("redis PUBLISH fb_agent:scan:finished failed")


# ====================== Heartbeat ======================


async def heartbeat_loop(redis_client, stop: asyncio.Event) -> None:
    """Периодически пишет worker:heartbeat:observer с TTL 60s.

    Параллельный таск — не блокирует main-loop сканирования.
    """
    if redis_client is None:
        return
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("observer heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ====================== One cycle ======================


# Перенесено в core.observer.accounts (single source — переиспользуется API-дашбордом
# для scan_blocked_reason). Алиас сохраняет существующий импорт/тесты.
_allowlist_blocks_scan = allowlist_blocks_scan


async def _notify_synced_disabled(engine, redis_client, *, fb_ad_ids: list[str]) -> None:
    """DM owner'у про тихий sync OFF→disabled (внешнее отключение ада). Best-effort."""
    for fb_ad_id in fb_ad_ids:
        text = (
            f"ℹ️ <b>Объявление помечено disabled</b>\n"
            f"fb_ad_id=<code>{fb_ad_id}</code> — в Meta уже OFF "
            f"(внешнее отключение/наш pause не подтвердился)."
        )
        await notify_owners(
            engine,
            redis_client,
            category="sync_disabled",
            text=text,
            dedup_key=f"sync_offline_disabled:{fb_ad_id}",
            dedup_ttl_seconds=21600,
        )


async def _run_account_scan(
    engine: AsyncEngine,
    *,
    gate: ScannerGate,
    config: dict,
    auto_recover_page: bool,
    redis_client=None,
    tg_client=None,
    ad_account_id: str | None = None,
    accounts_done: int | None = None,
    accounts_total: int | None = None,
) -> dict:
    """Скан ОДНОГО кабинета (или legacy-скан текущей вкладки при ad_account_id=None).

    Свой scan_run, свой process_scan_rows, свой TG-dispatch. Не бросает исключения
    наверх — ошибки пишутся в scan_runs.outcome (цикл по кабинетам продолжается).
    """
    scan_id = await _begin_scan_run(engine, ad_account_id=ad_account_id)
    started_monotonic = time.monotonic()
    await _publish_runtime_status(
        redis_client,
        status="scanning",
        active_phase="scan",
        current_account_id=ad_account_id,
        accounts_done=accounts_done,
        accounts_total=accounts_total,
    )

    cycle_result: CycleResult | None = None
    outcome = "success"
    error_msg: str | None = None
    dispatched: dict | None = None

    # Allowlist кампаний (observer_config.campaign_ids) — ГЛОБАЛЬНЫЙ. campaign.id уникальны
    # per кабинет → при НЕСКОЛЬКИХ кабинетах в чужом фильтр отсёк бы ВСЁ (скан пуст →
    # слепота, FSM не реагирует). Поэтому allowlist применяем, когда в scan set ОДИН кабинет
    # (его кампании = allowlist, фильтр безопасен) ИЛИ legacy-режим (ad_account_id=None);
    # при мульти-кабе (>1 кабинета) — игнор, скоупинг через owner_tag.
    single_cabinet = (accounts_total or 1) <= 1
    campaign_ids = list(config.get("campaign_ids") or []) if single_cabinet else []

    try:
        # Opt-in мониторинг: при ОДНОМ кабинете пустой allowlist = НИЧЕГО не отслеживаем
        # (раньше пусто = «все мои кампании»). Скан не гоняем — отдаём пустой результат,
        # FSM не трогается, авто-стоп по этим объявлениям не работает (так и задумано).
        if _allowlist_blocks_scan(single_cabinet, campaign_ids):
            scan_out = ScanCycleOutput(
                rows=[], empty_reason="ничего не отслеживается (allowlist пуст)"
            )
        else:
            scan_out = await gate.run_one_scan(
                campaign_ids=campaign_ids,
                owner_tag=config.get("owner_campaign_tag"),
                auto_recover_page=auto_recover_page,
                ad_account_id=ad_account_id,
            )

        if not scan_out.rows:
            outcome = "empty"
            error_msg = scan_out.empty_reason or "no rows"
        else:
            await _publish_runtime_status(
                redis_client,
                status="scanning",
                active_phase="parse",
                current_account_id=ad_account_id,
                accounts_done=accounts_done,
                accounts_total=accounts_total,
            )
            cycle_result = await process_scan_rows(
                engine,
                rows=scan_out.rows,
                scan_id=scan_id,
                owner_tag=config.get("owner_campaign_tag"),
                ad_account_id=ad_account_id,
            )

            # Нотификация owner'а при тихом sync OFF→disabled
            if cycle_result.synced_offline_disabled:
                try:
                    await _notify_synced_disabled(
                        engine, redis_client, fb_ad_ids=cycle_result.synced_offline_disabled
                    )
                except Exception:
                    logger.exception("sync_disabled notify failed — продолжаю")

            # Доставка алертов в TG — если был хоть один emit
            if (
                tg_client is not None
                and cycle_result
                and (cycle_result.alerts_warning + cycle_result.alerts_stop > 0)
            ):
                from core.telegram.alert_dispatcher import dispatch_pending_alerts

                await _publish_runtime_status(
                    redis_client,
                    status="scanning",
                    active_phase="dispatch",
                    current_account_id=ad_account_id,
                    accounts_done=accounts_done,
                    accounts_total=accounts_total,
                )
                try:
                    dispatched = await dispatch_pending_alerts(
                        engine,
                        client=tg_client,
                        scan_id=scan_id,
                        redis_client=redis_client,
                    )
                except Exception:
                    logger.exception("alert dispatch failed — продолжаю")
                    dispatched = {"sent": 0, "errors": 1}

            # Retry-sweep осиротевших алертов (TG-outage мог удалить pre-claim
            # без отправки; FSM уже в stop_sent → emit_alert=False → без sweep навсегда потерян).
            # Запускаем каждый цикл независимо от наличия новых алертов.
            if tg_client is not None:
                from core.telegram.alert_dispatcher import sweep_orphan_alerts

                try:
                    swept = await sweep_orphan_alerts(
                        engine, client=tg_client, redis_client=redis_client
                    )
                    if swept.get("sent"):
                        logger.info(
                            "sweep_orphan_alerts: досланы %d осиротевших алертов",
                            swept["sent"],
                        )
                except Exception:
                    logger.exception("sweep_orphan_alerts failed — продолжаю")
    except Exception as exc:
        logger.exception("scan cycle crashed (кабинет=%s): %s", ad_account_id or "-", exc)
        outcome = "error"
        error_msg = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    await _finish_scan_run(
        engine,
        scan_id=scan_id,
        outcome=outcome,
        cycle_result=cycle_result,
        error_message=error_msg,
        duration_ms=duration_ms,
    )

    await _publish_scan_finished(
        redis_client, scan_id=scan_id, outcome=outcome, cycle_result=cycle_result
    )

    return {
        "outcome": outcome,
        "scan_id": scan_id,
        "ad_account_id": ad_account_id,
        "duration_ms": duration_ms,
        "rows_total": cycle_result.rows_total if cycle_result else 0,
        "rows_with_offer": cycle_result.rows_with_offer if cycle_result else 0,
        "alerts_warning": cycle_result.alerts_warning if cycle_result else 0,
        "alerts_stop": cycle_result.alerts_stop if cycle_result else 0,
        "ads_in_warning_state": cycle_result.ads_in_warning_state if cycle_result else 0,
        "ads_in_stop_state": cycle_result.ads_in_stop_state if cycle_result else 0,
        "tg_dispatched": dispatched,
        "error": error_msg,
    }


# Пауза между кабинетами внутри одного цикла (анти-«дёрганье» вкладок очередью).
ACCOUNT_SCAN_PAUSE_SECONDS = 3.0


def _aggregate_cycle_summary(per_account: list[dict]) -> dict:
    """Свод цикла из per-account summary (для логов и адаптивного интервала).

    Семантика outcome (важно для Layer 3 degraded-трекинга и resolve_scan_mode):
      - "error"   — ТОЛЬКО если упали ВСЕ кабинеты (полный провал цикла);
      - "success" — хотя бы один кабинет отсканирован успешно;
      - "empty"   — остальное (все empty или смесь empty+error).
    Счётчики суммируются → worst-case агрегация для адаптивного интервала:
    stop-хит в любом кабинете даёт CRITICAL всему циклу.
    """
    outcomes = [s["outcome"] for s in per_account]
    if all(o == "error" for o in outcomes):
        outcome = "error"
    elif any(o == "success" for o in outcomes):
        outcome = "success"
    else:
        outcome = "empty"
    # error — только от реально упавших кабинетов: empty_reason ("no_active_ads")
    # НЕ ошибка и не должен светиться в summary success-цикла.
    first_error = next(
        (s["error"] for s in per_account if s["outcome"] == "error" and s.get("error")), None
    )
    return {
        "outcome": outcome,
        # Для совместимости с потребителями старого summary — последний scan_id.
        "scan_id": per_account[-1]["scan_id"] if per_account else None,
        "duration_ms": sum(s.get("duration_ms", 0) for s in per_account),
        "rows_total": sum(s.get("rows_total", 0) for s in per_account),
        "rows_with_offer": sum(s.get("rows_with_offer", 0) for s in per_account),
        "alerts_warning": sum(s.get("alerts_warning", 0) for s in per_account),
        "alerts_stop": sum(s.get("alerts_stop", 0) for s in per_account),
        "ads_in_warning_state": sum(s.get("ads_in_warning_state", 0) for s in per_account),
        "ads_in_stop_state": sum(s.get("ads_in_stop_state", 0) for s in per_account),
        "tg_dispatched": next(
            (s["tg_dispatched"] for s in per_account if s.get("tg_dispatched")), None
        ),
        "error": first_error,
        "accounts": [
            {"ad_account_id": s.get("ad_account_id"), "outcome": s["outcome"]} for s in per_account
        ],
    }


# Module-level: набор кабинетов, для которого уже выполнена подготовка (вкладки открыты).
# При смене набора (активирован новый оффер / поменялись ad_account_ids) — переподготовка.
# None = подготовка ещё не выполнялась (первый цикл после старта процесса).
_prepared_accounts: frozenset[str] | None = None


def _reset_prepared_accounts() -> None:
    """Сброс флага подготовки (для тестов / форс-переподготовки)."""
    global _prepared_accounts
    _prepared_accounts = None


# TTL дедупа TG-уведомлений фазы подготовки (по набору кабинетов): при устойчивом
# сбое Vision не спамить «Подготавливаю…» каждые ~90с — повтор не чаще этого окна.
PREPARE_TG_DEDUP_TTL_SECONDS = 3600


async def _notify_tg_simple(engine: AsyncEngine, tg_client, text: str) -> None:
    """Best-effort простое TG-уведомление (без дедупа). Не бросает."""
    if tg_client is None:
        return
    try:
        from core.telegram.service import load_telegram_config

        cfg = await load_telegram_config(engine)
        if cfg is None or cfg.chat_id is None:
            return
        await tg_client.send_message(chat_id=str(cfg.chat_id), text=text, parse_mode="HTML")
    except Exception:
        logger.exception("observer: не удалось отправить TG-уведомление подготовки")


async def _prepare_tg_allowed(redis_client, accounts: frozenset[str]) -> bool:
    """Дедуп TG-уведомлений подготовки по набору кабинетов (Redis SET NX EX).

    True → этот набор ещё не уведомляли в текущем окне (слать можно). False → уже слали
    (молчим, чтобы не спамить при повторных попытках того же набора). Redis недоступен →
    True (лучше уведомить, чем потерять).
    """
    if redis_client is None:
        return True
    import hashlib

    digest = hashlib.sha1(":".join(sorted(accounts)).encode()).hexdigest()[:16]
    key = f"observer:prepare:tg:{digest}"
    try:
        ok = await redis_client.set(key, "1", ex=PREPARE_TG_DEDUP_TTL_SECONDS, nx=True)
        return bool(ok)
    except Exception:
        logger.exception("observer: ошибка дедупа TG-уведомления подготовки")
        return True


async def _prepare_workspace(
    engine: AsyncEngine,
    *,
    gate: ScannerGate,
    accounts: list[str],
    redis_client=None,
    tg_client=None,
) -> None:
    """Фаза «подготовка рабочего места»: открыть вкладки кабинетов активных офферов
    перед сканом (manage/campaigns + колонки пользователя).

    Выполняется при первом цикле после старта и при изменении набора кабинетов
    (активирован новый оффер). Статус preparing → веб-панель + TG. Не блокирует скан:
    при сбое open_cabinet_tabs скан сам переоткроет вкладки по ходу (ensureAdsManagerPage).
    """
    global _prepared_accounts
    current = frozenset(accounts)
    if current == _prepared_accounts:
        return  # набор не менялся — вкладки уже открыты

    n = len(accounts)
    msg = f"Подготавливаю рабочее место: открываю кабинеты ({n})…"
    logger.info("observer: %s [%s]", msg, ", ".join(accounts))
    # Статус в runtime пишем всегда (дёшево, не спамит — это перезапись одного ключа).
    await _publish_runtime_status(
        redis_client,
        status="preparing",
        active_phase="preparing",
        status_message=msg,
        accounts_total=n,
    )

    # TG: дедуп по набору, чтобы при устойчивом сбое Vision не слать «Подготавливаю…»
    # каждый цикл (~90с). Стартовое и сообщение о ПОЛНОМ провале — под дедупом; итог
    # успеха шлём всегда (он происходит ровно один раз: дальше набор помечен prepared).
    notify_allowed = await _prepare_tg_allowed(redis_client, current)
    if notify_allowed:
        await _notify_tg_simple(engine, tg_client, f"🛠 {msg}")

    try:
        results = await gate.open_cabinet_tabs(accounts)
    except Exception:
        logger.exception("observer: фаза подготовки — open_cabinet_tabs упал")
        return  # не блокируем скан

    opened = [r for r in results if r.get("opened")]
    failed = [r for r in results if not r.get("opened")]
    if failed:
        logger.warning(
            "observer: не открылись кабинеты: %s",
            ", ".join(f"{r.get('ad_account_id')}({r.get('error', '')})" for r in failed),
        )
    logger.info("observer: подготовка завершена — открыто %d/%d кабинетов", len(opened), n)
    done_msg = f"✅ Кабинеты открыты ({len(opened)}/{n}), начинаю сканирование."
    if failed:
        done_msg += " Не открылись: " + ", ".join(str(r.get("ad_account_id")) for r in failed) + "."

    if opened:
        # Успех (хотя бы частичный) — итог шлём ВСЕГДА (один раз: набор станет prepared).
        await _notify_tg_simple(engine, tg_client, done_msg)
        _prepared_accounts = current
    elif notify_allowed:
        # Полный провал — сообщаем только в окне дедупа (иначе на следующем цикле попробуем
        # снова, но без TG-спама).
        await _notify_tg_simple(engine, tg_client, done_msg)


async def _maybe_alert_multi_cab_no_owner(
    engine: AsyncEngine,
    redis_client,
    tg_client,
    *,
    account_count: int,
) -> None:
    """Deduped ops-алерт R4: мульти-каб без owner_tag → скан остановлен ради безопасности.

    Дедуп через Redis SET NX EX (как degraded-алерт): повтор не чаще TTL, чтобы не
    спамить каждый цикл, пока конфиг не исправят. Redis недоступен → алерт всё равно
    шлём (лучше шумнее, чем пропустить money-критичный сигнал).
    """
    allowed = True
    if redis_client is not None:
        try:
            ok = await redis_client.set(
                MULTI_CAB_NO_OWNER_ALERT_DEDUP_KEY,
                "1",
                ex=MULTI_CAB_NO_OWNER_ALERT_TTL_SECONDS,
                nx=True,
            )
            allowed = bool(ok)
        except Exception:
            logger.exception("observer: ошибка SET дедупа multi_cab_no_owner")
            allowed = True
    if not allowed:
        return
    msg = (
        f"🚨 {fmt.b('Скан остановлен ради безопасности')}\n"
        f"Подключено кабинетов: {account_count}, но owner_campaign_tag не задан.\n"
        "В мульти-кабинете без тега бот оценивал бы стоп-правила и мог авто-стопнуть "
        "ЧУЖУЮ рекламу в общем кабинете.\n"
        "Задай owner_campaign_tag в настройках observer, чтобы скан возобновился."
    )
    logger.critical(
        "observer: мульти-каб (%d кабинетов) без owner_tag — скан остановлен ради безопасности",
        account_count,
    )
    await _notify_tg_simple(engine, tg_client, msg)


async def run_one_cycle(
    engine: AsyncEngine,
    *,
    gate: ScannerGate,
    redis_client=None,
    tg_client=None,
) -> dict:
    """Один полный цикл observer'а. Возвращает summary для логов/тестов.

    Мульти-кабинет (MULTI_CABINET_PLAN.md §M3): scan set = union offers.ad_account_ids
    активных офферов; кабинеты обходятся ПОСЛЕДОВАТЕЛЬНО, каждый со своим scan_run.
    Ошибка одного кабинета НЕ прерывает остальные. Пустой scan set → legacy-скан
    текущей вкладки (поведение до мульти-кабинетности).

    Не бросает исключения наверх — все ошибки логирует и записывает в scan_runs.outcome.

    Если tg_client передан — после process_scan_rows зовём dispatch_pending_alerts(scan_id):
    события записанные в этом scan'е улетают в TG чат с inline-кнопками.
    """
    config = await load_observer_config(engine)
    if config is None or not config["is_scanning_enabled"]:
        await _publish_runtime_status(redis_client, status="paused")
        return {"outcome": "paused", "scan_id": None}

    # Self-heal Layer 2 gate: разрешать ли клиенту эскалировать reconnect при пропаже вкладки.
    auto_recover_page = await load_vision_auto_restart_flag(engine)

    # Scan set кабинетов из активных офферов. Пустой → legacy одно-кабинетный скан.
    accounts = await resolve_scan_account_ids(engine)
    if not accounts:
        summary = await _run_account_scan(
            engine,
            gate=gate,
            config=config,
            auto_recover_page=auto_recover_page,
            redis_client=redis_client,
            tg_client=tg_client,
            ad_account_id=None,
        )
        await _publish_runtime_status(
            redis_client,
            status="idle",
            last_successful_scan_at=(
                datetime.now(timezone.utc) if summary["outcome"] == "success" else None
            ),
        )
        return summary

    # Money-гард R4: мульти-каб (>1 кабинета) без owner_tag → скоупинг чужих кампаний
    # отсутствует (allowlist в мульти-кабе игнорируется, campaign_matches_owner→True для
    # ВСЕХ). Без тега бот авто-стопнул бы чужую рекламу в shared-кабинете (необратимо).
    # Зеркалит single-cab guard (allowlist_blocks_scan): скан этого набора кабинетов НЕ
    # запускаем, шлём deduped ops-алерт. Возобновится, как только owner_tag будет задан.
    if multi_cabinet_requires_owner_tag(len(accounts), config.get("owner_campaign_tag")):
        await _maybe_alert_multi_cab_no_owner(
            engine, redis_client, tg_client, account_count=len(accounts)
        )
        await _publish_runtime_status(
            redis_client,
            status="idle",
            status_message="Скан остановлен: мульти-каб без owner_tag (безопасность)",
        )
        return {"outcome": "skipped", "scan_id": None, "reason": "multi_cab_no_owner_tag"}

    # Warning о выпавших из скана офферах (активны, но без кабинетов) — раз в цикл в лог.
    orphan_offers = await list_offers_without_accounts(engine)
    if orphan_offers:
        logger.warning(
            "observer: офферы без ad_account_ids не сканируются: %s",
            ", ".join(orphan_offers),
        )

    # Глобальный allowlist несовместим с мульти-кабом (>1 кабинета): см. _run_account_scan.
    # При одном кабинете в scan set allowlist применяется (фильтр по campaign.id безопасен).
    if config.get("campaign_ids") and len(accounts) > 1:
        logger.warning(
            "observer: мульти-каб (>1 кабинета) — allowlist campaign_ids (%d шт.) игнорируется, "
            "скоупинг только через owner_tag",
            len(config.get("campaign_ids") or []),
        )

    # Фаза «подготовка рабочего места»: открыть вкладки всех кабинетов (с колонками)
    # перед сканом. Идемпотентно: только при первом цикле / смене набора кабинетов.
    await _prepare_workspace(
        engine,
        gate=gate,
        accounts=accounts,
        redis_client=redis_client,
        tg_client=tg_client,
    )

    per_account: list[dict] = []
    for idx, account_id in enumerate(accounts):
        if idx > 0:
            # Короткая пауза между кабинетами — «человеческий» темп переключения вкладок.
            await asyncio.sleep(ACCOUNT_SCAN_PAUSE_SECONDS)
        summary = await _run_account_scan(
            engine,
            gate=gate,
            config=config,
            auto_recover_page=auto_recover_page,
            redis_client=redis_client,
            tg_client=tg_client,
            ad_account_id=account_id,
            accounts_done=idx,
            accounts_total=len(accounts),
        )
        per_account.append(summary)

    aggregated = _aggregate_cycle_summary(per_account)
    await _publish_runtime_status(
        redis_client,
        status="idle",
        last_successful_scan_at=(
            datetime.now(timezone.utc) if aggregated["outcome"] == "success" else None
        ),
    )
    return aggregated


# ====================== Shared state для pubsub-сигналов ======================


@dataclass
class _ObserverState:
    """Разделяемое состояние между main_loop и pubsub-handler'ами."""

    force_scan_pending: bool = False  # выставляется триггером fb_agent:observer:trigger
    should_stop: bool = False  # выставляется сигналом restart
    consecutive_scan_failures: int = 0  # подряд error-циклов (Layer 3 degraded-алерт)


async def _maybe_alert_degraded(
    engine: AsyncEngine,
    redis_client,
    *,
    consecutive_failures: int,
    last_error: str | None,
) -> bool:
    """Layer 3: deduped TG-алерт о «тихой» деградации observer'а.

    Срабатывает, когда сканы стабильно падают и self-heal (Layer 1/2) не восстановил
    primary-вкладку. Доставка через notify_recipients (telegram_recipients) — тот же
    путь, что у health_watchdog. Легаси telegram_config.chat_id убран (инцидент 01.07:
    chat_id NULL в проде → алерт молча терялся при сработавшем детекте).
    Дедуп через Redis SET NX EX; при недоставке дедуп снимается — следующий
    падающий цикл попробует доставить снова (алерт не теряется на TTL).
    Возвращает True, если алерт реально отправлен в TG.
    """
    if redis_client is None:
        return False
    # Дедуп ставим ПЕРВЫМ — чтобы не дёргать БД/TG на каждом падающем цикле (~90с).
    try:
        ok = await redis_client.set(
            DEGRADED_ALERT_DEDUP_KEY, "1", ex=DEGRADED_ALERT_TTL_SECONDS, nx=True
        )
    except Exception:
        logger.exception("observer degraded-alert: ошибка SET дедуп-ключа")
        return False
    if not ok:
        return False

    text_msg = (
        f"🚨 {fmt.b('Observer — деградация')}\n"
        f"{consecutive_failures} циклов подряд не удалось отсканировать кабинет, "
        "самовосстановление не помогло.\n"
        "Проверь Vision-профиль/браузер (вкладку Ads Manager на :3030).\n"
        f"Последняя ошибка: {fmt.code(str(last_error or 'н/д'))}"
    )
    logger.error("ALERT (observer degraded): %s", text_msg)

    sent = await notify_recipients(
        engine,
        redis_client,
        category="observer:degraded",
        text=text_msg,
    )
    if not sent:
        logger.warning(
            "observer degraded-алерт НЕ доставлен (нет получателей/токена или сбой TG) — "
            "проверь telegram_recipients; дедуп снят, ретрай на следующем цикле"
        )
        try:
            await redis_client.delete(DEGRADED_ALERT_DEDUP_KEY)
        except Exception:
            logger.exception("observer degraded-alert: ошибка DEL дедуп-ключа после недоставки")
    return bool(sent)


async def _clear_degraded_dedup(redis_client) -> None:
    """Сбрасывает дедуп degraded-алерта при восстановлении — следующая деградация алертит сразу."""
    if redis_client is None:
        return
    try:
        await redis_client.delete(DEGRADED_ALERT_DEDUP_KEY)
    except Exception:
        logger.exception("observer degraded-alert: ошибка DEL дедуп-ключа")


async def _wait_interruptible(*events: asyncio.Event, seconds: float) -> None:
    """Спит до ``seconds``, но просыпается раньше, если любой из ``events`` выставлен.

    Нужен, чтобы scan-now (trigger) реально прерывал sleep между циклами, а не ждал
    полного интервала. Не бросает по таймауту; корректно отменяет и дренирует waiter'ы.
    """
    waiters = [asyncio.ensure_future(e.wait()) for e in events]
    try:
        await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waiters:
            w.cancel()
        for w in waiters:
            try:
                await w
            except (asyncio.CancelledError, Exception):
                pass


async def _sleep_with_runtime_refresh(
    redis_client,
    *events: asyncio.Event,
    seconds: float,
    status: str = "idle",
    next_scan_at: datetime | None = None,
    scan_mode: str | None = None,
    last_successful_scan_at: datetime | None = None,
) -> None:
    """Спит до ``seconds`` (прерываясь на любой из ``events``), освежая observer:runtime.

    Сразу при входе публикует целевое состояние сна (next_scan_at + scan_mode), чтобы фронт
    получил реальный адаптивный отсчёт и режим немедленно: на интервалах короче
    RUNTIME_REFRESH_SECONDS публикации в цикле могло не быть вовсе (CALM 90с < 120с), и эти
    поля не доезжали — UI сидел на mock-отсчёте «всегда база».

    Длинный sleep бьётся на чанки ≤ RUNTIME_REFRESH_SECONDS; после каждого чанка, если
    ни один event не выставлен и сон не закончился, переписываем observer:runtime со
    свежим updated_at. Так health_watchdog не считает observer «протухшим» (TTL/stale)
    даже на длинных интервалах. Прерываемость scan-now/shutdown сохраняется.

    ``status`` сохраняет фактическое состояние между сканами ("paused" на паузе, иначе
    "idle"), чтобы освежение не затирало paused-статус ложным running.
    """

    async def _refresh() -> None:
        await _publish_runtime_status(
            redis_client,
            status=status,
            next_scan_at=next_scan_at,
            scan_mode=scan_mode,
            last_successful_scan_at=last_successful_scan_at,
        )

    await _refresh()
    remaining = float(seconds)
    while remaining > 0:
        chunk = min(remaining, float(RUNTIME_REFRESH_SECONDS))
        await _wait_interruptible(*events, seconds=chunk)
        if any(e.is_set() for e in events):
            return
        remaining -= chunk
        if remaining > 0:
            await _refresh()


# ====================== Main loop ======================


async def main_loop(
    *,
    gate_factory: Callable[[], Awaitable[ScannerGate]] | None = None,
    redis_factory: Callable[[], Awaitable[object]] | None = None,
    tg_client_factory: Callable[[], Awaitable[object]] | None = None,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Бесконечный цикл observer.

    Args:
        gate_factory: создаёт ScannerGate (default: BrowserAgentClient wrapper).
        redis_factory: создаёт redis.asyncio.Redis для heartbeat.
        tg_client_factory: создаёт TelegramBotClient для отправки алертов.
            Если None — алерты не отправляются (полезно в тестах).
        should_continue: для тестов — управляет выходом из цикла.
    """
    from core.control.pubsub_listener import RedisPubSubListener

    db_url = _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)

    if gate_factory is None:
        gate_factory = _default_gate_factory
    if redis_factory is None:
        redis_factory = _default_redis_factory
    if tg_client_factory is None:
        # bind engine в default factory чтобы интерфейс остался Callable[[], Awaitable]
        async def _bound_tg_factory():
            return await _default_tg_client_factory(engine)

        tg_client_factory = _bound_tg_factory

    # Graceful shutdown по SIGTERM/SIGINT.
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    # Разделяемое состояние для pubsub-handler'ов.
    state = _ObserverState()
    # Событие пробуждения sleep'а по scan-now (отдельно от shutdown_event).
    trigger_event = asyncio.Event()

    gate: ScannerGate | None = None
    redis_client = None
    tg_client = None
    listener_task: asyncio.Task | None = None
    listener: RedisPubSubListener | None = None
    heartbeat_task: asyncio.Task | None = None

    try:
        redis_client = await redis_factory()

        # Heartbeat стартуем СРАЗУ после Redis — до загрузки TG-конфига из БД и gate,
        # чтобы health_watchdog не считал observer мёртвым во время инициализации.
        if redis_client is not None:
            heartbeat_task = asyncio.create_task(heartbeat_loop(redis_client, shutdown_event))

        tg_client = await tg_client_factory()
        logger.info("observer_worker запущен")

        # Подписываемся на управляющие каналы если Redis доступен.
        if redis_client is not None:
            listener = RedisPubSubListener(
                redis_client,
                [CHANNEL_TRIGGER, CHANNEL_CABINET_DAY, CHANNEL_RESTART],
            )

            async def _on_trigger(_payload: dict) -> None:
                """Форс-скан: будим sleep (trigger_event) и выставляем флаг."""
                logger.info("observer: получен trigger scan-now")
                state.force_scan_pending = True
                trigger_event.set()

            async def _on_cabinet_day(_payload: dict) -> None:
                """Новый кабинетный день → форс-рескан (тот же механизм, что scan-now).

                Зачем рескан: единственный publisher cabinet_day — ручной эндпоинт
                POST /observer/start-new-cabinet-day, который шлёт ТОЛЬКО cabinet_day
                (не trigger). Без этой реакции ручной старт нового дня не вызывал
                немедленный скан — observer ждал штатный интервал. Рескан сразу
                подхватывает обнулённые суточные метрики.

                Чего НЕ делаем: архив уже пишет сам эндпоинт-публишер (синхронно,
                до publish) — дублировать нельзя. In-memory стейта на «день» у
                observer'а нет (config грузится каждый цикл, FSM — в БД), сбрасывать
                нечего. Owner-scoping сохраняется — рескан читает свой скоуп по тегу.
                """
                logger.info("observer: получен сигнал cabinet_day — форс-рескан нового дня")
                state.force_scan_pending = True
                trigger_event.set()

            async def _on_restart(_payload: dict) -> None:
                """Graceful restart: выставляем should_stop + shutdown_event."""
                logger.info("observer: получен сигнал restart по каналу %s", CHANNEL_RESTART)
                state.should_stop = True
                shutdown_event.set()

            listener.register(CHANNEL_TRIGGER, _on_trigger)
            listener.register(CHANNEL_CABINET_DAY, _on_cabinet_day)
            listener.register(CHANNEL_RESTART, _on_restart)
            listener_task = asyncio.create_task(listener.run_forever())

        while should_continue() and not shutdown_event.is_set() and not state.should_stop:
            if gate is None:
                try:
                    gate = await gate_factory()
                except Exception:
                    logger.exception("Не смог создать gate — sleep 10s")
                    await asyncio.sleep(10.0)
                    continue

            try:
                summary = await run_one_cycle(
                    engine,
                    gate=gate,
                    redis_client=redis_client,
                    tg_client=tg_client,
                )
                logger.info("cycle done: %s", summary)
            except Exception as exc:
                # MID-6 (аудит 02.07): падение ВНЕ _run_account_scan (например DB-ошибка
                # в load_observer_config/resolve_scan_account_ids) раньше просто
                # пересоздавало gate и уходило на следующую итерацию молча — мимо Layer 3
                # degraded-детектора (он считает только summary["outcome"] == "error" из
                # штатного пути). Теперь такой краш тоже засчитывается в тот же счётчик
                # consecutive_scan_failures — иначе воркер мог биться в этой ветке часами
                # (heartbeat/observer:runtime живы) без единого алерта.
                logger.exception("run_one_cycle crashed — пересоздаю gate")
                gate = None
                state.consecutive_scan_failures += 1
                if state.consecutive_scan_failures >= DEGRADED_ALERT_THRESHOLD:
                    await _maybe_alert_degraded(
                        engine,
                        redis_client,
                        consecutive_failures=state.consecutive_scan_failures,
                        last_error=f"{type(exc).__name__}: {exc}",
                    )
                await asyncio.sleep(10.0)
                continue

            # Layer 3: трекинг «тихой» деградации — N подряд error-циклов → degraded-алерт.
            if summary.get("outcome") == "error":
                state.consecutive_scan_failures += 1
                if state.consecutive_scan_failures >= DEGRADED_ALERT_THRESHOLD:
                    await _maybe_alert_degraded(
                        engine,
                        redis_client,
                        consecutive_failures=state.consecutive_scan_failures,
                        last_error=summary.get("error"),
                    )
            elif state.consecutive_scan_failures:
                # Скан восстановился — сброс счётчика и дедупа, чтобы новая деградация алертила сразу.
                state.consecutive_scan_failures = 0
                await _clear_degraded_dedup(redis_client)

            # Если выставлен форс-скан — немедленно делаем следующий цикл без sleep.
            if state.force_scan_pending:
                logger.info("observer: force_scan_pending — пропускаю sleep, запускаю сразу")
                state.force_scan_pending = False
                trigger_event.clear()
                continue

            # Адаптивный интервал: база (UI-слайдер interval_seconds) = CALM-режим,
            # частота скана зависит от угрозы в этом цикле (у порога — чаще).
            config = await load_observer_config(engine)
            base_interval = float((config or {}).get("interval_seconds", 90))
            scan_mode = resolve_scan_mode(summary)
            interval = compute_adaptive_interval(base_interval, scan_mode)
            # Jitter ±10% от рассчитанного интервала (anti-detect), с тем же clamp по нижней границе.
            jitter_offset = interval * JITTER_FRACTION
            sleep_for = clamp_interval(interval + random.uniform(-jitter_offset, jitter_offset))
            logger.info(
                "observer: режим=%s интервал=%.0fс (база=%.0f, со сдвигом=%.0f)",
                scan_mode,
                interval,
                base_interval,
                sleep_for,
            )

            # Sleep, прерываемый shutdown'ом ИЛИ trigger'ом scan-now. На длинных
            # интервалах освежаем observer:runtime, чтобы watchdog не считал нас протухшими.
            # На паузе сохраняем статус "paused" (не затираем ложным "idle").
            next_scan_at = datetime.now(timezone.utc) + timedelta(seconds=sleep_for)
            runtime_status = "paused" if summary.get("outcome") == "paused" else "idle"
            # last_successful_scan_at пишем тем же контрактом, что и финальный publish цикла
            # (now при success, иначе None) — чтобы освежение сна не затирало «последний скан».
            last_ok_at = datetime.now(timezone.utc) if summary.get("outcome") == "success" else None
            await _sleep_with_runtime_refresh(
                redis_client,
                shutdown_event,
                trigger_event,
                seconds=sleep_for,
                status=runtime_status,
                next_scan_at=next_scan_at,
                scan_mode=scan_mode,
                last_successful_scan_at=last_ok_at,
            )

            # Если trigger пришёл во время sleep — сбрасываем флаги, цикл идёт сразу.
            if trigger_event.is_set() or state.force_scan_pending:
                logger.info("observer: trigger во время sleep — запускаю скан немедленно")
                trigger_event.clear()
                state.force_scan_pending = False
    finally:
        logger.info("observer_worker завершён")

        # Останавливаем heartbeat-таск.
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        # Останавливаем pubsub-listener.
        if listener is not None:
            try:
                await listener.stop()
            except Exception:
                pass
        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass

        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                pass
        await engine.dispose()


# ====================== Default factories (прод-реализация) ======================


async def _default_gate_factory() -> ScannerGate:
    """Прод-реализация: оборачивает BrowserAgentClient в ScannerGate-протокол."""
    from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
    from clients.python_grpc.client import ScanResult as GrpcScanResult
    from core.config import get_settings, reveal_secret

    s = get_settings()
    client = BrowserAgentClient(
        BrowserAgentConfig(
            # grpc_host/port из env — иначе в Docker observer пойдёт на localhost
            # внутри контейнера и не достучится до browser-agent на хосте
            # (host.docker.internal). Консистентно с meta_api/creator-воркерами.
            grpc_host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
            grpc_port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
            vision_x_token=reveal_secret(s.vision_x_token),
            vision_api_url=s.vision_api_url,
            vision_profile_id=s.vision_profile_id,
        )
    )
    await client.start()
    # run_scan_cycle сам поднимет browser-сессию (ensure_browser_session внутри).

    class _BrowserAgentScannerGate:
        async def run_one_scan(
            self,
            campaign_ids: list[str] | None = None,
            owner_tag: str | None = None,
            auto_recover_page: bool = True,
            ad_account_id: str | None = None,
        ) -> ScanCycleOutput:
            final_result: GrpcScanResult | None = None
            async for event in client.run_scan_cycle(
                campaign_ids=campaign_ids or [],
                owner_tag=owner_tag,
                auto_recover_page=auto_recover_page,
                ad_account_id=ad_account_id,
            ):
                # ScanProgress нам пока не нужен — слушаем только финальный ScanResult
                if isinstance(event, GrpcScanResult):
                    final_result = event
            if final_result is None:
                return ScanCycleOutput(rows=[], empty_reason="no final result")
            return ScanCycleOutput(
                rows=final_result.rows,
                total_passes=final_result.total_passes,
                duration_seconds=final_result.duration_seconds,
                empty_reason=final_result.empty_reason,
                warnings=list(final_result.warnings),
            )

        async def open_cabinet_tabs(self, ad_account_ids: list[str]) -> list[dict]:
            return await client.open_cabinet_tabs(ad_account_ids)

    return _BrowserAgentScannerGate()


async def _default_redis_factory():
    """Прод-реализация: redis.asyncio.Redis к docker-compose:6380."""
    try:
        import redis.asyncio as redis_async  # type: ignore
    except ImportError:
        logger.warning("redis package не установлен — heartbeat отключён")
        return None

    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        return redis_async.from_url(redis_url, decode_responses=True)

    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("REDIS_PORT", "6380"))
    return redis_async.Redis(host=host, port=port, decode_responses=True)


async def _default_tg_client_factory(engine):
    """Прод-реализация: TelegramBotClient из telegram_config (если есть)."""
    import httpx

    from core.telegram.client import TelegramBotClient
    from core.telegram.service import load_telegram_config

    cfg = await load_telegram_config(engine)
    if cfg is None or not cfg.bot_token:
        logger.warning("telegram_config пустой — алерты в TG отключены")
        return None
    return TelegramBotClient(bot_token=cfg.bot_token, http_client=httpx.AsyncClient(timeout=30.0))
