# -*- coding: utf-8 -*-
"""Health Watchdog main loop.

Раз в CHECK_INTERVAL_SECONDS:
- читает Redis-ключи ``worker:heartbeat:<name>`` для каждого имени из EXPECTED_WORKERS;
  отсутствие ключа (TTL истёк) → шлёт алерт в Telegram (с дедупом 1 ч/воркер).
- читает JSON ``observer:runtime``; если ключа нет или ``updated_at`` старше
  OBSERVER_STALE_AFTER_SECONDS → отдельный алерт ``observer worker stale``.

Сам watchdog пишет ``worker:heartbeat:health_watchdog`` TTL 60s.

Graceful shutdown по SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.telegram.client import TelegramAPIError, TelegramBotClient
from core.telegram.service import load_telegram_config

logger = logging.getLogger("health_watchdog")

WORKER_NAME = "health_watchdog"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

CHECK_INTERVAL_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_INTERVAL_SEC", "60"))
ALERT_DEDUP_TTL_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_ALERT_TTL_SEC", "3600"))
OBSERVER_STALE_AFTER_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_OBSERVER_STALE_SEC", "300"))
DEFAULT_EXPECTED_WORKERS = "observer,disable,enable,telegram_poller,cleanup,reconciler,meta_api"

OBSERVER_RUNTIME_KEY = "observer:runtime"
ALERT_DEDUP_PREFIX = "health:alerted:"


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


# ====================== Telegram алерты ======================


async def _send_alert(
    tg_client: TelegramBotClient | None,
    *,
    chat_id: str | None,
    thread_id: int | None,
    text: str,
) -> None:
    """Отправляет TG-алерт. Если клиента/чата нет — пишет только в лог."""
    logger.warning("ALERT: %s", text)
    if tg_client is None or not chat_id:
        return
    try:
        await tg_client.send_message(
            chat_id=chat_id,
            text=text,
            message_thread_id=thread_id,
            parse_mode=None,
        )
    except TelegramAPIError as exc:
        logger.error("не удалось отправить TG-алерт: %s", exc)
    except Exception:  # noqa: BLE001
        logger.exception("неожиданная ошибка при отправке TG-алерта")


async def _maybe_alert_with_dedup(
    redis_client: redis_asyncio.Redis,
    *,
    dedup_key: str,
    text: str,
    tg_client: TelegramBotClient | None,
    chat_id: str | None,
    thread_id: int | None,
) -> bool:
    """Атомарно ставит дедуп-ключ (NX+EX) и шлёт алерт, если ключа не было.

    Возвращает True, если алерт был отправлен.
    """
    try:
        ok = await redis_client.set(
            dedup_key,
            "1",
            ex=ALERT_DEDUP_TTL_SECONDS,
            nx=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("ошибка SET дедуп-ключа %s", dedup_key)
        return False

    if not ok:
        return False

    await _send_alert(tg_client, chat_id=chat_id, thread_id=thread_id, text=text)
    return True


# ====================== проверки ======================


async def check_worker_heartbeats(
    redis_client: redis_asyncio.Redis,
    *,
    expected_workers: list[str],
    tg_client: TelegramBotClient | None,
    chat_id: str | None,
    thread_id: int | None,
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
            f"🚨 Health Watchdog: воркер '{name}' не дышит "
            f"более {HEARTBEAT_TTL_SECONDS // 60} мин (heartbeat истёк)"
        )
        sent = await _maybe_alert_with_dedup(
            redis_client,
            dedup_key=dedup_key,
            text=text,
            tg_client=tg_client,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        if sent:
            alerted += 1
    return alerted


async def check_observer_runtime(
    redis_client: redis_asyncio.Redis,
    *,
    tg_client: TelegramBotClient | None,
    chat_id: str | None,
    thread_id: int | None,
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

    text = f"🚨 Health Watchdog: observer:runtime устарел ({reason})"
    return await _maybe_alert_with_dedup(
        redis_client,
        dedup_key=dedup_key,
        text=text,
        tg_client=tg_client,
        chat_id=chat_id,
        thread_id=thread_id,
    )


async def run_one_check(
    redis_client: redis_asyncio.Redis,
    *,
    expected_workers: list[str],
    tg_client: TelegramBotClient | None,
    chat_id: str | None,
    thread_id: int | None,
) -> None:
    """Один прогон: heartbeat'ы + observer:runtime."""
    await check_worker_heartbeats(
        redis_client,
        expected_workers=expected_workers,
        tg_client=tg_client,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    await check_observer_runtime(
        redis_client,
        tg_client=tg_client,
        chat_id=chat_id,
        thread_id=thread_id,
    )


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
    tg_client: TelegramBotClient | None,
    chat_id: str | None,
    thread_id: int | None,
    stop: asyncio.Event,
) -> None:
    """Главный цикл проверок раз в CHECK_INTERVAL_SECONDS."""
    while not stop.is_set():
        try:
            await run_one_check(
                redis_client,
                expected_workers=expected_workers,
                tg_client=tg_client,
                chat_id=chat_id,
                thread_id=thread_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("ошибка в цикле проверок")
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


# ====================== entrypoint ======================


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


async def _load_tg(
    engine: AsyncEngine,
) -> tuple[TelegramBotClient | None, str | None, int | None]:
    """Читает telegram_config и собирает (client, chat_id, thread_id) либо (None,None,None)."""
    try:
        cfg = await load_telegram_config(engine)
    except Exception:  # noqa: BLE001
        logger.exception("не удалось загрузить telegram_config")
        return None, None, None

    if cfg is None or not cfg.bot_token or cfg.chat_id is None:
        logger.warning("telegram_config не настроен — алерты только в лог")
        return None, None, None

    client = TelegramBotClient(cfg.bot_token)
    return client, str(cfg.chat_id), cfg.forum_ops_thread_id


async def main_loop(database_url: str | None = None) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, echo=False)
    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)

    expected_workers = parse_expected_workers(
        os.environ.get("EXPECTED_WORKERS", DEFAULT_EXPECTED_WORKERS)
    )
    if not expected_workers:
        logger.warning("EXPECTED_WORKERS пуст — heartbeat-проверки не выполняются")

    tg_client, chat_id, thread_id = await _load_tg(engine)

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
        await asyncio.gather(
            heartbeat_loop(redis_client, stop),
            check_loop(
                redis_client,
                expected_workers=expected_workers,
                tg_client=tg_client,
                chat_id=chat_id,
                thread_id=thread_id,
                stop=stop,
            ),
        )
    finally:
        if tg_client is not None:
            try:
                await tg_client.close()
            except Exception:  # noqa: BLE001
                logger.exception("ошибка закрытия TG-клиента")
        try:
            await redis_client.aclose()
        except Exception:  # noqa: BLE001
            logger.exception("ошибка закрытия Redis-клиента")
        await engine.dispose()
        logger.info("health_watchdog остановлен")
