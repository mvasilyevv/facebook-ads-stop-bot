# -*- coding: utf-8 -*-
"""Health Watchdog main loop.

Раз в CHECK_INTERVAL_SECONDS:
- читает Redis-ключи ``worker:heartbeat:<name>`` для каждого имени из EXPECTED_WORKERS;
  отсутствие ключа (TTL истёк) → шлёт алерт в Telegram (с дедупом 1 ч/воркер).
- читает JSON ``observer:runtime``; если ключа нет или ``updated_at`` старше
  OBSERVER_STALE_AFTER_SECONDS → отдельный алерт ``observer worker stale``.
- (money-критично) проверяет канал авто-стопа: застрявшие задачи pause_ad/bot_auto_stop
  и рассинхрон FSM=stop_sent ↔ delivery_status=ACTIVE → CRITICAL-алерт в ops-топик
  (см. check_autostop_channel; нужен доступ к Postgres через engine).

Сам watchdog пишет ``worker:heartbeat:health_watchdog`` TTL 60s.

Graceful shutdown по SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Any, Protocol

import redis.asyncio as redis_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.db import WORKER_ENGINE_KWARGS
from core.pubsub import CHANNEL_HEALTH_UPDATED
from core.telegram.worker_notify import notify_recipients

logger = logging.getLogger("health_watchdog")

WORKER_NAME = "health_watchdog"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

CHECK_INTERVAL_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_INTERVAL_SEC", "60"))
# Пауза перед перезапуском упавшего цикла (_supervised, инцидент 01.07:
# gather без защиты — одно исключение молча гасило весь воркер-сторож).
LOOP_RESTART_DELAY_SECONDS = float(os.environ.get("HEALTH_WATCHDOG_LOOP_RESTART_SEC", "5"))
ALERT_DEDUP_TTL_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_ALERT_TTL_SEC", "3600"))
OBSERVER_STALE_AFTER_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_OBSERVER_STALE_SEC", "300"))
# Grace-период перед ПЕРВОЙ проверкой: при совместном старте (supervisord/run.sh)
# воркеры ещё инициализируются (Redis/БД/browser-agent) и не успели записать первый
# heartbeat. Без задержки watchdog слал ложный «воркер не дышит» сразу при старте.
STARTUP_GRACE_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_STARTUP_GRACE_SEC", "90"))
# Синхронизировано с воркерами run.sh (= health_details._DEFAULT_EXPECTED_WORKERS).
# Money-критичные обязаны мониториться: meta_api (канал отключения/включения pause_ad/
# activate_ad), cabinet_scheduler (автостарт кабинета по расписанию), tracker_aggregator
# (агрегатор депозитов) — их зависание (heartbeat-stall при живом процессе) не должно
# пройти без TG-алерта (H4). enable_reco — реальное heartbeat-имя enable_recommendation_worker.
# health_watchdog себя НЕ мониторит: если он сам мёртв — алертить некому.
# browser-agent — нативный systemd-сервис на хосте (мост к Vision/каналу авто-стопа),
# пишет worker:heartbeat:browser-agent (reconnect к Redis починен, resilience-аудит rank 1).
# Самый хрупкий money-компонент — обязан мониториться.
DEFAULT_EXPECTED_WORKERS = (
    "observer,telegram_poller,cleanup,reconciler,meta_api,tracker_aggregator,"
    "enable_reco,cabinet_scheduler,digest_scheduler,creator,creator_recorder,"
    "campaign_creator,browser-agent"
)

OBSERVER_RUNTIME_KEY = "observer:runtime"
ALERT_DEDUP_PREFIX = "health:alerted:"

# ====================== канал авто-стопа (money-критичный мониторинг) ======================
# Инцидент 2026-06-19: канал исполнения авто-стопа (Marketing API через Vision
# page.evaluate(fetch)) лёг — fetch начал падать «Failed to fetch» (code=-2). Задачи
# task_queue pause_ad/bot_auto_stop зависли в retrying (15-16 из 72 попыток), объявления
# остались в FSM=stop_sent, но delivery_status=ACTIVE и продолжали тратить. Сигнала не было.
# Watchdog независимым внешним наблюдателем ловит отказ канала по двум триггерам:
#   (3) задачи pause_ad/bot_auto_stop незавершены дольше STUCK_MIN — прямой признак отказа;
#   (2) рассинхрон stop_sent при delivery_status=ACTIVE дольше DESYNC_MIN — money-симптом.
AUTOSTOP_STUCK_AFTER_MINUTES = int(os.environ.get("HEALTH_WATCHDOG_AUTOSTOP_STUCK_MIN", "15"))
AUTOSTOP_DESYNC_AFTER_MINUTES = int(os.environ.get("HEALTH_WATCHDOG_AUTOSTOP_DESYNC_MIN", "15"))
# Максимум объявлений в каждом списке алерта — остальное сворачивается в «… и ещё N»
# (защита от раздувания TG-сообщения сверх лимита 4096 при массовом отказе).
AUTOSTOP_ALERT_MAX_ITEMS = 10
AUTOSTOP_DEDUP_KEY = f"{ALERT_DEDUP_PREFIX}autostop_channel"

# ====================== сетевой probe канала Marketing API (money-критичный) ======================
# Инцидент 2026-06-19: token-only health давал false-positive «healthy» при мёртвом
# сетевом канале (Failed to fetch). Watchdog — единственный прободер: раз в
# META_PROBE_INTERVAL_SECONDS делает реальный GET /me (full_probe) через browser-agent,
# пишет результат в Redis meta_api:channel:health (его читает health_details), а при
# отказе канала шлёт CRITICAL-алерт. Проактивно дополняет БД-детектор (check_autostop_channel).
META_PROBE_INTERVAL_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_META_PROBE_SEC", "300"))
META_CHANNEL_HEALTH_KEY = "meta_api:channel:health"
META_CHANNEL_DEDUP_KEY = f"{ALERT_DEDUP_PREFIX}meta_channel"
# TTL ключа = 2× интервал: если сам прободер (watchdog) мёртв, ключ протухает и
# health_details показывает UNKNOWN, а не залипший «healthy».
META_CHANNEL_HEALTH_TTL_SECONDS = META_PROBE_INTERVAL_SECONDS * 2


class _MetaProbeClient(Protocol):
    """Минимальный контракт MetaApiClient для probe (для тестируемости)."""

    async def check_health(self, *, full_probe: bool = ...) -> dict[str, Any]: ...


# ====================== pure helpers (тестируем напрямую) ======================


def parse_expected_workers(env_value: str | None) -> list[str]:
    """Парсит CSV ``EXPECTED_WORKERS`` → нормализованный список имён.

    Пустые элементы и пробелы отбрасываются. Дубликаты схлопываются с сохранением порядка.
    """
    if not env_value:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in env_value.split(","):
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def check_observer_runtime_freshness(
    payload_json: str | None,
    *,
    now: datetime,
    max_age_seconds: int = OBSERVER_STALE_AFTER_SECONDS,
) -> tuple[bool, str | None]:
    """Проверяет свежесть ``observer:runtime``.

    Возвращает ``(is_stale, reason)``. Если ключа нет — stale с reason ``missing``.
    Если JSON битый — stale с reason ``invalid_json``.
    Если updated_at старше max_age_seconds — stale с reason вида ``stale (X min)``.
    Иначе — ``(False, None)``.
    """
    if payload_json is None:
        return True, "missing"

    try:
        payload = json.loads(payload_json)
    except (ValueError, TypeError):
        return True, "invalid_json"

    if not isinstance(payload, dict):
        return True, "invalid_json"

    updated_raw = payload.get("updated_at")
    if not isinstance(updated_raw, str) or not updated_raw:
        return True, "missing_updated_at"

    try:
        updated_at = datetime.fromisoformat(updated_raw)
    except ValueError:
        return True, "invalid_updated_at"

    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    age_seconds = (now - updated_at).total_seconds()
    if age_seconds > max_age_seconds:
        return True, f"stale ({int(age_seconds // 60)} min)"

    return False, None


def should_alert(heartbeat_value: str | None, dedup_value: str | None) -> bool:
    """Алертим, когда heartbeat истёк И дедуп-ключа ещё нет."""
    return heartbeat_value is None and dedup_value is None


@dataclass(frozen=True)
class StuckPauseTask:
    """Застрявшая задача авто-стопа (pause_ad от bot_auto_stop, не дошла до succeeded)."""

    task_id: int
    target_id: str  # fb_ad_id объявления, которое не удалось остановить
    attempt_count: int
    age_minutes: int  # сколько минут задача не завершается с момента создания
    last_error: str | None  # последняя ошибка (часто «Failed to fetch» при отказе канала)


@dataclass(frozen=True)
class DesyncedStopAd:
    """Рассинхрон: объявление в FSM=stop_sent, но delivery_status=ACTIVE (крутится/тратит)."""

    fb_ad_id: str
    age_minutes: int  # сколько минут держится рассинхрон (с момента перехода в stop_sent)


def build_autostop_channel_alert(
    stuck_tasks: Sequence[StuckPauseTask],
    desynced_ads: Sequence[DesyncedStopAd],
) -> str | None:
    """Pure: текст CRITICAL-алерта по триггерам отказа канала авто-стопа.

    Возвращает None, если оба триггера пусты (канал здоров). Иначе — единое
    HTML-сообщение: раздел застрявших задач pause_ad и/или раздел рассинхрона
    stop_sent↔ACTIVE. Длинные списки усекаются до AUTOSTOP_ALERT_MAX_ITEMS с
    пометкой «… и ещё N» (TG-лимит 4096).
    """
    if not stuck_tasks and not desynced_ads:
        return None

    lines = [
        "🆘 <b>КРИТИЧНО: канал авто-стопа</b>",
        "Авто-стоп не доводит объявления до OFF — деньги тратятся.",
    ]

    if stuck_tasks:
        lines.append("")
        lines.append(f"⛔️ Застряли задачи pause_ad (bot_auto_stop): <b>{len(stuck_tasks)}</b>")
        for task in stuck_tasks[:AUTOSTOP_ALERT_MAX_ITEMS]:
            err = f", ошибка: {html.escape(task.last_error)}" if task.last_error else ""
            lines.append(
                f"   • <code>{html.escape(task.target_id)}</code> — "
                f"{task.age_minutes} мин, попыток {task.attempt_count}{err}"
            )
        if len(stuck_tasks) > AUTOSTOP_ALERT_MAX_ITEMS:
            lines.append(f"   … и ещё {len(stuck_tasks) - AUTOSTOP_ALERT_MAX_ITEMS}")

    if desynced_ads:
        lines.append("")
        lines.append(
            f"🔌 Рассинхрон (FSM=stop_sent, но delivery_status=ACTIVE): <b>{len(desynced_ads)}</b>"
        )
        for ad in desynced_ads[:AUTOSTOP_ALERT_MAX_ITEMS]:
            lines.append(f"   • <code>{html.escape(ad.fb_ad_id)}</code> — {ad.age_minutes} мин")
        if len(desynced_ads) > AUTOSTOP_ALERT_MAX_ITEMS:
            lines.append(f"   … и ещё {len(desynced_ads) - AUTOSTOP_ALERT_MAX_ITEMS}")

    lines.append("")
    lines.append("Проверь Vision-сессию и meta_api_worker.")
    return "\n".join(lines)


def classify_meta_probe(probe: dict[str, Any]) -> tuple[bool, str]:
    """Классифицирует результат check_health(full_probe=True): жив ли канал.

    Возвращает ``(is_down, reason)``. Канал мёртв (is_down=True), если probe вернул
    ``healthy=False`` — это покрывает network-down (Failed to fetch), протухший токен (190),
    недоступность browser-agent (circuit_open) и отсутствие токена. Meta-side ошибки
    (rate-limit) оставляют ``healthy=True`` → канал жив (не считаем outage'ом, согласовано
    с ``core.meta_api.autostop_alert.is_channel_down_error``).

    reason — наиболее информативная причина: ``probe_detail`` при выполненном probe,
    иначе ``detail`` (например circuit_open / token_not_found).
    """
    if bool(probe.get("healthy", False)):
        return False, str(probe.get("probe_detail") or probe.get("detail") or "ok")

    if probe.get("probe_performed"):
        reason = probe.get("probe_detail") or probe.get("detail") or "down"
    else:
        reason = probe.get("detail") or "unreachable"
    return True, str(reason)


def build_meta_channel_alert(*, reason: str, detail: str) -> str:
    """CRITICAL-текст: канал Marketing API (auto-stop) мёртв по проактивному probe."""
    return (
        "🛑 <b>CRITICAL: канал Marketing API мёртв (probe)</b>\n"
        f"Реальный GET /me к graph.facebook.com не прошёл: <code>{html.escape(reason)}</code>\n"
        f"Детали: <code>{html.escape(str(detail)[:200])}</code>\n\n"
        "⚠️ Money: авто-стоп (pause_ad) не доходит до Meta — объявления могут тратить бюджет.\n"
        "Почини Vision-канал (reconnect/restart browser_agent или Vision-профиль) "
        "или выключи объявления вручную в Ads Manager."
    )


# ====================== Telegram алерты ======================


async def _maybe_alert_with_dedup(
    redis_client: redis_asyncio.Redis,
    *,
    dedup_key: str,
    text: str,
    engine: AsyncEngine,
) -> bool:
    """Сначала отправляет алерт, SET NX ставит ТОЛЬКО при успешной отправке.

    Порядок: GET(dedup_key) → если стоит, пропускаем; иначе отправка всем recipients
    через notify_recipients → SET NX EX только при sent=True. Это гарантирует, что при
    сбое TG ключ не блокирует повторную попытку на TTL (алерт не теряется).
    Возвращает True, если алерт был успешно отправлен и дедуп установлен.
    """
    # Дедуп-проверка: уже алертили в этом окне?
    try:
        if await redis_client.get(dedup_key):
            # Явный след: без него успешная отправка и подавление неотличимы от
            # зависания (расследование 01.07 приняло тихий дедуп/успех за провал).
            logger.info("алерт %s подавлен дедупом (уже отправлен в этом окне)", dedup_key)
            return False
    except Exception:  # noqa: BLE001
        logger.exception("ошибка чтения дедуп-ключа %s", dedup_key)

    # Рассылаем всем активным recipients (без forum-топика)
    sent = await notify_recipients(
        engine,
        redis_client,
        category=f"health_watchdog:{dedup_key}",
        text=text,
    )
    if not sent:
        return False

    logger.info("алерт %s отправлен активным recipients", dedup_key)
    # Ставим дедуп только после успешной доставки
    try:
        await redis_client.set(dedup_key, "1", ex=ALERT_DEDUP_TTL_SECONDS, nx=True)
    except Exception:  # noqa: BLE001
        logger.exception("ошибка SET дедуп-ключа %s", dedup_key)
    return True


# ====================== проверки ======================


async def check_worker_heartbeats(
    redis_client: redis_asyncio.Redis,
    *,
    expected_workers: list[str],
    engine: AsyncEngine,
) -> int:
    """Для каждого ожидаемого воркера проверяет heartbeat. Возвращает число алертов."""
    alerted = 0
    for name in expected_workers:
        hb_key = f"worker:heartbeat:{name}"
        dedup_key = f"{ALERT_DEDUP_PREFIX}{name}"
        try:
            hb_value = await redis_client.get(hb_key)
        except Exception:  # noqa: BLE001
            logger.exception("ошибка GET %s", hb_key)
            continue

        if hb_value is not None:
            continue

        try:
            dedup_value = await redis_client.get(dedup_key)
        except Exception:  # noqa: BLE001
            logger.exception("ошибка GET %s", dedup_key)
            continue

        if not should_alert(hb_value, dedup_value):
            continue

        text = (
            f"🚨 <b>Health Watchdog</b>\n"
            f"Воркер <b>{html.escape(name)}</b> не дышит более "
            f"{HEARTBEAT_TTL_SECONDS // 60} мин — heartbeat истёк."
        )
        sent = await _maybe_alert_with_dedup(
            redis_client,
            dedup_key=dedup_key,
            text=text,
            engine=engine,
        )
        if sent:
            alerted += 1
    return alerted


async def check_observer_runtime(
    redis_client: redis_asyncio.Redis,
    *,
    engine: AsyncEngine,
) -> bool:
    """Проверяет ``observer:runtime``. Возвращает True, если алерт был отправлен."""
    dedup_key = f"{ALERT_DEDUP_PREFIX}observer_runtime"
    try:
        payload_json = await redis_client.get(OBSERVER_RUNTIME_KEY)
    except Exception:  # noqa: BLE001
        logger.exception("ошибка GET %s", OBSERVER_RUNTIME_KEY)
        return False

    is_stale, reason = check_observer_runtime_freshness(
        payload_json,
        now=datetime.now(timezone.utc),
    )
    if not is_stale:
        return False

    text = f"🚨 <b>Health Watchdog</b>\nobserver:runtime устарел: {html.escape(reason)}."
    return await _maybe_alert_with_dedup(
        redis_client,
        dedup_key=dedup_key,
        text=text,
        engine=engine,
    )


_STUCK_PAUSE_TASKS_SQL = text(
    """
    SELECT id,
           payload->>'target_id' AS target_id,
           attempt_count,
           last_error,
           CAST(FLOOR(EXTRACT(EPOCH FROM (NOW() - created_at)) / 60) AS INTEGER) AS age_minutes
    FROM task_queue
    WHERE task_type = 'meta_api_mutation'
      AND payload->>'mutation_kind' = 'pause_ad'
      AND requested_by = 'bot_auto_stop'
      AND status IN ('pending', 'retrying', 'running')
      AND created_at < NOW() - make_interval(mins => :minutes)
    ORDER BY created_at ASC
    """
)

_DESYNCED_STOP_ADS_SQL = text(
    """
    SELECT fb_ads.fb_ad_id,
           CAST(FLOOR(EXTRACT(EPOCH FROM (NOW() - s.last_transition_at)) / 60) AS INTEGER)
               AS age_minutes
    FROM fb_ads
    JOIN ad_alert_state s ON s.ad_id = fb_ads.id
    WHERE s.alert_state = 'stop_sent'
      AND UPPER(fb_ads.delivery_status) = 'ACTIVE'
      AND s.last_transition_at < NOW() - make_interval(mins => :minutes)
    ORDER BY s.last_transition_at ASC
    """
)


async def query_stuck_pause_tasks(engine: AsyncEngine, *, minutes: int) -> list[StuckPauseTask]:
    """Задачи авто-стопа (pause_ad/bot_auto_stop), не завершённые дольше ``minutes`` минут.

    Триггер 3: незавершённый статус (pending/retrying/running) + возраст created_at >
    порога = прямой признак отказа канала исполнения (Vision fetch лёг, токен протух и т.п.).
    """
    async with engine.connect() as conn:
        result = await conn.execute(_STUCK_PAUSE_TASKS_SQL, {"minutes": int(minutes)})
        rows = result.all()
    return [
        StuckPauseTask(
            task_id=int(r[0]),
            target_id=str(r[1]),
            attempt_count=int(r[2]),
            last_error=r[3],
            age_minutes=int(r[4]),
        )
        for r in rows
    ]


async def query_desynced_stop_ads(engine: AsyncEngine, *, minutes: int) -> list[DesyncedStopAd]:
    """Объявления в FSM=stop_sent, но delivery_status=ACTIVE дольше ``minutes`` минут.

    Триггер 2 (money-симптом): авто-стоп вынес решение (stop_sent), но не довёл объявление
    до OFF — оно крутится и тратит. Ловит проблему по финальному симптому независимо от
    причины. delivery_status сравнивается без учёта регистра; NULL (не сканировали) не
    считается ACTIVE — не алертим вслепую.
    """
    async with engine.connect() as conn:
        result = await conn.execute(_DESYNCED_STOP_ADS_SQL, {"minutes": int(minutes)})
        rows = result.all()
    return [DesyncedStopAd(fb_ad_id=str(r[0]), age_minutes=int(r[1])) for r in rows]


async def check_autostop_channel(
    engine: AsyncEngine,
    redis_client: redis_asyncio.Redis,
    *,
    stuck_after_minutes: int = AUTOSTOP_STUCK_AFTER_MINUTES,
    desync_after_minutes: int = AUTOSTOP_DESYNC_AFTER_MINUTES,
) -> bool:
    """Проверяет здоровье канала авто-стопа. Возвращает True, если алерт был отправлен.

    Money-критично: при отказе канала исполнения авто-стопа (инцидент 2026-06-19)
    объявления остаются крутиться при FSM=stop_sent. Шлёт CRITICAL всем активным
    recipients (без forum-топика) с дедупом (раз в час, пока проблема жива).
    """
    try:
        stuck = await query_stuck_pause_tasks(engine, minutes=stuck_after_minutes)
        desynced = await query_desynced_stop_ads(engine, minutes=desync_after_minutes)
    except Exception:  # noqa: BLE001
        logger.exception("ошибка проверки канала авто-стопа")
        return False

    alert_text = build_autostop_channel_alert(stuck, desynced)
    if alert_text is None:
        return False

    logger.error("канал авто-стопа деградировал: stuck=%d desync=%d", len(stuck), len(desynced))
    return await _maybe_alert_with_dedup(
        redis_client,
        dedup_key=AUTOSTOP_DEDUP_KEY,
        text=alert_text,
        engine=engine,
    )


async def check_meta_api_channel(
    meta_client: _MetaProbeClient,
    redis_client: redis_asyncio.Redis,
    *,
    engine: AsyncEngine,
    now: datetime | None = None,
) -> bool:
    """Проактивный probe канала Marketing API. Возвращает True, если алерт отправлен.

    Единственный прободер: делает реальный GET /me (full_probe) через browser-agent,
    пишет снимок в Redis ``meta_api:channel:health`` (его читает health_details), при
    отказе канала шлёт CRITICAL с дедупом, при восстановлении снимает дедуп (re-arm).
    Best-effort: исключения check_health трактуются как «канал мёртв».
    """
    now = now or datetime.now(UTC)

    # Сканирование выключено (намеренно) → observer не держит постоянную browser-agent
    # сессию, и «сессия не найдена» это НЕ отказ канала, а ожидаемое состояние. Не шлём
    # ложный CRITICAL (money-спам). Логика совпадает с observer: config None / false → пауза.
    try:
        from core.observer.queries import load_observer_config

        obs_config = await load_observer_config(engine)
        scanning_on = bool(obs_config and obs_config.get("is_scanning_enabled"))
    except Exception:  # noqa: BLE001
        # Ошибка чтения конфига — ведём себя как раньше (проверяем канал, можем алертить).
        scanning_on = True

    if not scanning_on:
        # След намеренного пропуска: после 11:24 01.07 probe молчал «по дизайну»,
        # и тишина в логах выглядела как зависание воркера.
        logger.info("meta probe: сканирование выключено — канал авто-стопа не проверяется")
        payload = {
            "healthy": False,
            "probe_performed": False,
            "probe_ok": False,
            "probe_status_code": 0,
            "probe_duration_ms": 0,
            "detail": "сканирование выключено — канал авто-стопа не проверяется",
            "probe_detail": "scanning_disabled",
            "reason": "сканирование выключено",
            "checked_at": now.isoformat(),
        }
        try:
            await redis_client.set(
                META_CHANNEL_HEALTH_KEY,
                json.dumps(payload, ensure_ascii=False),
                ex=META_CHANNEL_HEALTH_TTL_SECONDS,
            )
            # Снимаем дедуп: при включении сканирования + реальном отказе снова дадим алерт.
            await redis_client.delete(META_CHANNEL_DEDUP_KEY)
        except Exception:  # noqa: BLE001
            logger.exception("meta probe: запись статуса при выключенном сканировании")
        return False

    try:
        probe = await meta_client.check_health(full_probe=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("meta probe: check_health бросил исключение")
        probe = {
            "healthy": False,
            "detail": f"probe_exception: {exc}",
            "probe_performed": False,
            "probe_ok": False,
            "probe_status_code": 0,
            "probe_duration_ms": 0,
            "probe_detail": "probe_exception",
        }

    is_down, reason = classify_meta_probe(probe)

    payload = {
        "healthy": bool(probe.get("healthy", False)),
        "probe_performed": bool(probe.get("probe_performed", False)),
        "probe_ok": bool(probe.get("probe_ok", False)),
        "probe_status_code": int(probe.get("probe_status_code", 0) or 0),
        "probe_duration_ms": int(probe.get("probe_duration_ms", 0) or 0),
        "detail": str(probe.get("detail", "")),
        "probe_detail": str(probe.get("probe_detail", "")),
        "reason": reason,
        "checked_at": now.isoformat(),
    }
    try:
        await redis_client.set(
            META_CHANNEL_HEALTH_KEY,
            json.dumps(payload, ensure_ascii=False),
            ex=META_CHANNEL_HEALTH_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.exception("meta probe: не удалось записать %s", META_CHANNEL_HEALTH_KEY)

    if not is_down:
        # INFO-след каждого healthy-прохода: тишина в логах ≠ живой probe (урок 01.07).
        logger.info("meta probe: канал жив (%s)", reason)
        # Канал жив → снимаем дедуп, чтобы будущий отказ снова дал алерт (re-arm).
        try:
            await redis_client.delete(META_CHANNEL_DEDUP_KEY)
        except Exception:  # noqa: BLE001
            logger.exception("meta probe: не удалось снять дедуп")
        return False

    logger.error("канал Marketing API мёртв (probe): %s", reason)
    return await _maybe_alert_with_dedup(
        redis_client,
        dedup_key=META_CHANNEL_DEDUP_KEY,
        text=build_meta_channel_alert(reason=reason, detail=str(probe.get("detail", ""))),
        engine=engine,
    )


async def _publish_health_updated(
    redis_client: redis_asyncio.Redis,
    *,
    expected_workers: list[str],
) -> None:
    """Best-effort publish сводки здоровья воркеров в fb_agent:health:updated."""
    offline: list[str] = []
    for name in expected_workers:
        hb_key = f"worker:heartbeat:{name}"
        try:
            val = await redis_client.get(hb_key)
            if val is None:
                offline.append(name)
        except Exception:
            offline.append(name)

    if len(offline) == 0:
        overall = "HEALTHY"
    elif len(offline) < len(expected_workers):
        overall = "DEGRADED"
    else:
        overall = "CRITICAL"

    try:
        payload = json.dumps(
            {
                "overall": overall,
                "offline": offline,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
        await redis_client.publish(CHANNEL_HEALTH_UPDATED, payload)
    except Exception:
        logger.warning("health_watchdog: не удалось publish в %s", CHANNEL_HEALTH_UPDATED)


async def run_one_check(
    redis_client: redis_asyncio.Redis,
    *,
    expected_workers: list[str],
    engine: AsyncEngine,
) -> None:
    """Один прогон: heartbeat'ы + observer:runtime + канал авто-стопа + publish health:updated."""
    await check_worker_heartbeats(
        redis_client,
        expected_workers=expected_workers,
        engine=engine,
    )
    await check_observer_runtime(
        redis_client,
        engine=engine,
    )
    await check_autostop_channel(engine, redis_client)
    # Публикуем сводку health в Redis-канал (best-effort)
    await _publish_health_updated(redis_client, expected_workers=expected_workers)


# ====================== loops ======================


async def heartbeat_loop(redis_client: redis_asyncio.Redis, stop: asyncio.Event) -> None:
    """Периодически обновляет worker:heartbeat:health_watchdog."""
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def check_loop(
    redis_client: redis_asyncio.Redis,
    *,
    expected_workers: list[str],
    stop: asyncio.Event,
    engine: AsyncEngine,
) -> None:
    """Главный цикл проверок раз в CHECK_INTERVAL_SECONDS.

    Перед ПЕРВОЙ проверкой выжидает STARTUP_GRACE_SECONDS — даёт воркерам стартовать
    и записать первый heartbeat (иначе ложный «не дышит» при совместном старте).
    """
    # Grace при старте, прерываемый shutdown'ом.
    try:
        await asyncio.wait_for(stop.wait(), timeout=STARTUP_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass

    while not stop.is_set():
        try:
            await run_one_check(
                redis_client,
                expected_workers=expected_workers,
                engine=engine,
            )
        except Exception:  # noqa: BLE001
            logger.exception("ошибка в цикле проверок")
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def meta_probe_loop(
    meta_client: _MetaProbeClient,
    redis_client: redis_asyncio.Redis,
    *,
    stop: asyncio.Event,
    engine: AsyncEngine,
    interval: int = META_PROBE_INTERVAL_SECONDS,
) -> None:
    """Цикл сетевого probe канала Marketing API раз в ``interval`` секунд.

    Отдельная (более редкая) каденция от check_loop: реальный fetch к Meta не должен
    выполняться слишком часто. Перед первой проверкой выжидает STARTUP_GRACE_SECONDS
    (browser-agent/Vision стартуют дольше). Best-effort: ошибки не валят цикл.
    """
    try:
        await asyncio.wait_for(stop.wait(), timeout=STARTUP_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass

    while not stop.is_set():
        try:
            await check_meta_api_channel(
                meta_client,
                redis_client,
                engine=engine,
            )
        except Exception:  # noqa: BLE001
            logger.exception("ошибка в meta_probe_loop")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _supervised(
    name: str,
    factory: Any,
    stop: asyncio.Event,
) -> None:
    """Перезапускает упавший цикл вместо тихой смерти (инцидент 01.07).

    main_loop раньше собирал циклы голым asyncio.gather: одно исключение гасило
    весь воркер-сторож, а «сторожа за сторожем» нет — молчание длилось часами.
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


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


async def main_loop(database_url: str | None = None) -> None:
    from core.meta_api.client import MetaApiClient

    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)
    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)

    expected_workers = parse_expected_workers(
        os.environ.get("EXPECTED_WORKERS", DEFAULT_EXPECTED_WORKERS)
    )
    if not expected_workers:
        logger.warning("EXPECTED_WORKERS пуст — heartbeat-проверки не выполняются")

    # MetaApiClient для сетевого probe канала auto-stop (eager-init: gRPC-канал ленивый,
    # старт не блокирует; недоступность browser-agent probe-цикл трактует как «канал мёртв»).
    meta_client = MetaApiClient(
        host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
    )
    await meta_client.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info(
        "health_watchdog запущен (workers=%s, interval=%ss)",
        expected_workers,
        CHECK_INTERVAL_SECONDS,
    )
    try:
        # Каждый цикл под _supervised + return_exceptions: упавший цикл
        # перезапускается, а не гасит воркер молча (инцидент 01.07).
        await asyncio.gather(
            _supervised("heartbeat_loop", lambda: heartbeat_loop(redis_client, stop), stop),
            _supervised(
                "check_loop",
                lambda: check_loop(
                    redis_client,
                    expected_workers=expected_workers,
                    stop=stop,
                    engine=engine,
                ),
                stop,
            ),
            _supervised(
                "meta_probe_loop",
                lambda: meta_probe_loop(
                    meta_client,
                    redis_client,
                    stop=stop,
                    engine=engine,
                ),
                stop,
            ),
            return_exceptions=True,
        )
    finally:
        try:
            await meta_client.close()
        except Exception:  # noqa: BLE001
            logger.exception("ошибка закрытия MetaApiClient")
        try:
            await redis_client.aclose()
        except Exception:  # noqa: BLE001
            logger.exception("ошибка закрытия Redis-клиента")
        await engine.dispose()
        logger.info("health_watchdog остановлен")
