# -*- coding: utf-8 -*-
"""Digest scheduler — раз в минуту фиксирует daily digest в PostgreSQL-outbox.

Контракт:
- Окно: ``DIGEST_HOUR_UTC:DIGEST_MIN_UTC`` и до конца суток UTC (default 09:00 UTC).
  Catch-up: если scheduler упал в 09:02, а поднялся в 12:00 — digest всё равно
  будет поставлен в outbox. Лучше поздний digest, чем никакой.
- Защита от повторов: unique ``notification_events.dedupe_key`` в PostgreSQL.
- Process liveness is exported through the worker Prometheus endpoint.
- Получатели и доставка разрешаются notification worker после commit события.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.ai_assistant.pulse import collect_pulse_signals
from core.config import get_settings
from core.db import WORKER_ENGINE_KWARGS
from core.telegram.digest_builder import build_digest
from core.telegram.notifications import enqueue_notification
from core.telegram.schemas import NotificationCardFacts, NotificationEventSpec
from core.worker_metrics import mark_worker_heartbeat

logger = logging.getLogger("digest_scheduler")

WORKER_NAME = "digest_scheduler"
_METRICS_INTERVAL_SECONDS = 15.0

# Главный цикл — раз в минуту (как и health_watchdog).
CHECK_INTERVAL_SECONDS = int(os.environ.get("DIGEST_CHECK_INTERVAL_SEC", "60"))

# MID-11 (аудит 02.07): пауза перед перезапуском упавшего цикла (_supervised, по
# образцу apps/health_watchdog/main.py, коммит 246000c7) — раньше голый gather
# без этой обёртки: одно необработанное исключение в tick_loop гасило ВЕСЬ
# scheduler молча, до следующего рестарта процесса.
LOOP_RESTART_DELAY_SECONDS = float(os.environ.get("DIGEST_LOOP_RESTART_SEC", "5"))

# Плановое время дайджеста в UTC.
DIGEST_HOUR_UTC = int(os.environ.get("DIGEST_HOUR_UTC", "9"))
DIGEST_MIN_UTC = int(os.environ.get("DIGEST_MIN_UTC", "0"))
# ====================== pure helpers ======================


@dataclass(frozen=True)
class DigestWindow:
    """Спецификация планового окна отправки digest."""

    hour: int
    minute: int


def is_in_send_window(now: datetime, window: DigestWindow) -> bool:
    """True если now попадает в [HH:MM ; конец суток UTC).

    Catch-up семантика: окно открыто от планового времени до конца суток.
    Защита от повторов реализована ``notification_events.dedupe_key``,
    не самим окном. Если scheduler упал в 09:02 — поднявшись в 12:00,
    он всё равно отправит digest (ключа ещё нет). На следующие сутки
    dedupe key изменится (новая дата) и окно снова откроется.

    Hard cut-off на 23:59 UTC.
    """
    if now.tzinfo is None:
        raise ValueError("now должен быть timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    target_minutes = window.hour * 60 + window.minute
    current_minutes = now_utc.hour * 60 + now_utc.minute
    # 24*60 = 1440 — следующие сутки уже не «сегодняшний» digest.
    return target_minutes <= current_minutes < 24 * 60


async def _notification_exists(engine: AsyncEngine, dedupe_key: str) -> bool:
    """Use PostgreSQL, not Redis, as the notification dedupe authority."""
    async with engine.connect() as conn:
        return bool(
            await conn.scalar(
                text("SELECT 1 FROM notification_events WHERE dedupe_key = :key LIMIT 1"),
                {"key": dedupe_key},
            )
        )


async def run_one_tick(
    *,
    engine: AsyncEngine,
    now: datetime,
    window: DigestWindow,
) -> str:
    """Build a deterministic digest and commit it to the PostgreSQL outbox.

    Возвращает короткий статус ('out_of_window' / 'already_sent' /
    'queued').

    """
    if not is_in_send_window(now, window):
        return "out_of_window"

    event_dedupe_key = f"daily-digest:{now.astimezone(timezone.utc):%Y-%m-%d}"
    if await _notification_exists(engine, event_dedupe_key):
        return "already_sent"

    payload = await build_digest(engine, day_start_utc=now)
    money_ready = (
        payload.money_state == "ready"
        and payload.currency is not None
        and payload.total_spend_window is not None
    )
    top_lines = (
        [
            (f"Топ: {row.offer_code or row.ad_name} · {format(row.spend, 'f')} {row.currency}")
            for row in payload.top_ads_by_spend[:2]
        ]
        if money_ready
        else []
    )
    money_summary = (
        f"Spend {format(payload.total_spend_window, 'f')} {payload.currency}"
        if money_ready
        else "Spend не подтверждён"
    )
    money_issue_lines = [f"Деньги: {payload.money_issues[0]}"] if payload.money_issues else []
    result = await enqueue_notification(
        engine,
        NotificationEventSpec(
            event_type="daily_digest",
            severity=(
                "warning" if payload.alerts_stop_count or payload.disable_tasks_failed else "ok"
            ),
            audience="all",
            facts=NotificationCardFacts(
                title=f"Дайджест · {payload.window_start_utc:%Y-%m-%d}",
                summary=(
                    f"{money_summary} · "
                    f"warning {payload.alerts_warning_count} · "
                    f"critical {payload.alerts_stop_count}"
                ),
                lines=[
                    (
                        f"Отключения: {payload.disable_tasks_succeeded} confirmed · "
                        f"{payload.disable_tasks_failed} failed"
                    ),
                    (
                        f"Активно: {payload.active_offers_count} офферов · "
                        f"{payload.active_ads_count} объявлений"
                    ),
                    *money_issue_lines,
                    *top_lines,
                ],
            ),
            dedupe_key=event_dedupe_key,
        ),
    )
    return "queued" if result.was_created else "already_sent"


# ====================== пульс кабинета (2-3 слота в день) ======================


def parse_pulse_slots(raw: str) -> list[tuple[int, int]]:
    """Разобрать 'HH:MM,HH:MM,...' → [(hour, minute), ...]. Битые слоты пропускаются."""
    slots: list[tuple[int, int]] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        try:
            h, m = part.split(":", 1)
            hour, minute = int(h), int(m)
        except ValueError:
            continue
        if 0 <= hour < 24 and 0 <= minute < 60:
            slots.append((hour, minute))
    return sorted(set(slots))


def _due_pulse_slot(
    now: datetime, slots: list[tuple[int, int]]
) -> tuple[tuple[int, int], datetime] | None:
    """Последний наступивший слот + начало его окна (предыдущий слот или 00:00 UTC).

    Catch-up как у дайджеста: слот «должен» до конца суток, дедуп — PostgreSQL.
    Окно сигналов = [предыдущий слот; сейчас) — слоты не пересекаются и не дырявят день.
    """
    now_utc = now.astimezone(timezone.utc)
    cur = now_utc.hour * 60 + now_utc.minute
    due = [s for s in slots if s[0] * 60 + s[1] <= cur]
    if not due:
        return None
    slot = due[-1]
    prev_minutes = due[-2][0] * 60 + due[-2][1] if len(due) >= 2 else 0
    window_start = now_utc.replace(
        hour=prev_minutes // 60, minute=prev_minutes % 60, second=0, microsecond=0
    )
    return slot, window_start


async def run_pulse_tick(
    *,
    engine: AsyncEngine,
    now: datetime,
) -> str:
    """Один проход пульса: слот → deterministic signals → durable outbox.

    Статусы: 'disabled' / 'no_slot' / 'already_sent' / 'quiet' / 'queued'.
    """
    settings = get_settings()
    if not settings.ai_pulse_enabled:
        return "disabled"

    slots = parse_pulse_slots(settings.ai_pulse_slots_utc)
    due = _due_pulse_slot(now, slots)
    if due is None:
        return "no_slot"
    slot, window_start = due

    event_dedupe_key = (
        f"operator-pulse:{now.astimezone(timezone.utc):%Y-%m-%d}:{slot[0]:02d}{slot[1]:02d}"
    )
    if await _notification_exists(engine, event_dedupe_key):
        return "already_sent"

    signals = await collect_pulse_signals(engine, since=window_start, now=now)
    if signals is None:
        # Тихий тик не создаёт notification event. Следующий тик может
        # подхватить новый критичный сигнал в том же временном окне.
        return "quiet"

    result = await enqueue_notification(
        engine,
        NotificationEventSpec(
            event_type="operator_pulse",
            severity="critical" if signals.failed_tasks_count else "warning",
            audience="all",
            facts=NotificationCardFacts(
                title="Пульс кабинета",
                summary=(
                    f"Остановлено {signals.stop_count} · warnings {signals.warning_count} · "
                    f"failed actions {signals.failed_tasks_count}"
                ),
                lines=[
                    f"Стоп: {ad_name}{f' · {offer}' if offer else ''}"
                    for ad_name, offer, _rules in signals.top_stops[:3]
                ],
            ),
            dedupe_key=event_dedupe_key,
            scheduled_at=(
                None
                if signals.failed_tasks_count
                else now.astimezone(timezone.utc) + timedelta(minutes=5)
            ),
        ),
    )
    logger.info("pulse queued (slot %02d:%02d)", slot[0], slot[1])
    return "queued" if result.was_created else "already_sent"


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
    window: DigestWindow,
    stop: asyncio.Event,
) -> None:
    """Основной цикл — раз в минуту прогоняет run_one_tick."""
    while not stop.is_set():
        try:
            now = datetime.now(timezone.utc)
            status = await run_one_tick(
                engine=engine,
                now=now,
                window=window,
            )
            if status not in ("out_of_window", "already_sent"):
                logger.info("digest tick status=%s", status)
        except Exception:
            logger.exception("Ошибка в digest tick")
        try:
            pulse_status = await run_pulse_tick(
                engine=engine,
                now=datetime.now(timezone.utc),
            )
            if pulse_status not in ("disabled", "no_slot", "already_sent"):
                logger.info("pulse tick status=%s", pulse_status)
        except Exception:
            logger.exception("Ошибка в pulse tick")
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _supervised(
    name: str,
    factory: Any,
    stop: asyncio.Event,
) -> None:
    """Перезапускает упавший цикл вместо тихой смерти (MID-11, по образцу health_watchdog).

    factory — zero-arg callable, возвращающий корутину цикла; цикл сам крутится
    до stop. Исключение → лог + пауза LOOP_RESTART_DELAY_SECONDS + новый запуск.
    """
    while not stop.is_set():
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(
                "цикл %s упал — перезапуск через %sс", name, LOOP_RESTART_DELAY_SECONDS
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=LOOP_RESTART_DELAY_SECONDS)
            except asyncio.TimeoutError:
                pass


# ====================== entrypoint ======================


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


async def main_loop(
    database_url: str | None = None,
) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)

    window = DigestWindow(
        hour=DIGEST_HOUR_UTC,
        minute=DIGEST_MIN_UTC,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info(
        "digest_scheduler запущен (start=%02d:%02d UTC, tick=%ss)",
        window.hour,
        window.minute,
        CHECK_INTERVAL_SECONDS,
    )
    try:
        # Каждый цикл под _supervised: упавший цикл перезапускается, а не гасит
        # весь воркер молча (MID-11).
        await asyncio.gather(
            _supervised("metrics_loop", lambda: metrics_loop(stop), stop),
            _supervised(
                "tick_loop",
                lambda: tick_loop(
                    engine=engine,
                    window=window,
                    stop=stop,
                ),
                stop,
            ),
            return_exceptions=True,
        )
    finally:
        await engine.dispose()
        logger.info("digest_scheduler остановлен")


__all__ = [
    "DigestWindow",
    "_supervised",
    "is_in_send_window",
    "main_loop",
    "metrics_loop",
    "parse_pulse_slots",
    "run_one_tick",
    "run_pulse_tick",
    "tick_loop",
]
