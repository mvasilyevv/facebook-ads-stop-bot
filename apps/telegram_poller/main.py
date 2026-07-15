# -*- coding: utf-8 -*-
"""Telegram poller — минимальный long-polling loop под новую схему БД.

Запускается через run_telegram_poller.py. Перезагружает bot_token из БД
периодически — ротация в UI подхватывается горячо. Если токена нет (свежая БД
или он удалён) — poller НЕ падает, а уходит в idle-режим: продолжает heartbeat
и ждёт, пока токен введут через Settings (UI), затем сам начинает polling.
Это позволяет поднять API+UI без введённого токена (онбординг чистой инсталляции).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

import httpx
import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from core.db import WORKER_ENGINE_KWARGS
from core.pubsub import RedisPubSub
from core.telegram.bot_handler import handle_update
from core.telegram.client import TelegramAPIError, TelegramBotClient
from core.telegram.service import (
    load_telegram_config,
    save_poller_offset,
    touch_poller_heartbeat,
)

logger = logging.getLogger(__name__)

# Heartbeat — имя ДОЛЖНО совпадать с EXPECTED_WORKERS в health_watchdog.
WORKER_NAME = "telegram_poller"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

_HEARTBEAT_INTERVAL_SECONDS = 30
_TOKEN_RELOAD_INTERVAL_SECONDS = 60
_ERROR_RETRY_DELAY_SECONDS = 3
_LONG_POLL_TIMEOUT_SECONDS = 25
# Как часто в idle-режиме (нет токена) перечитывать config в ожидании ввода через UI.
_IDLE_RELOAD_INTERVAL_SECONDS = 10
# MID-7 (аудит 02.07): Telegram отвечает 409 Conflict на getUpdates, если параллельно
# запущен второй поллер с тем же токеном (два процесса/деплоя одновременно). Раньше это
# падало в общий except и ретраилось через 3с — ретрай-шторм двух процессов, дерущихся
# за offset. Держим отдельный (более долгий) backoff специально для этого случая.
_CONFLICT_RETRY_DELAY_SECONDS = 30

# MID-8 (аудит 02.07): offset подтверждался ДАЖЕ для упавшего handle_update → money-кнопка
# (dis:/ereco:) под алертом терялась навсегда (at-most-once). Фикс: при падении обработчика
# НЕ двигаем offset за этот update — Telegram переотдаст его на следующем poll (at-least-once).
# Защита от «ядовитого» update (падает вечно → поллер застрял на нём, всё встало): считаем
# попытки per-update. После _MAX_UPDATE_ATTEMPTS — скип с ERROR-логом (offset двигаем дальше),
# чтобы один битый update не заморозил очередь. Счётчик в Redis (переживает рестарт поллера,
# иначе два процесса/деплой сбрасывали бы его → вечный ретрай); при недоступности Redis —
# in-memory fallback (secondary cap), чтобы всё равно не залипнуть навсегда.
_MAX_UPDATE_ATTEMPTS = 3
_UPDATE_FAIL_KEY_PREFIX = "tg:upd:fail:"
_UPDATE_FAIL_TTL_SECONDS = 3600
# In-memory fallback-счётчик попыток per update_id (если Redis недоступен).
_inmem_update_fail_counts: dict[int, int] = {}


async def _bump_update_failure(redis_client, update_id: int) -> int:
    """Инкремент счётчика неудачных попыток обработки update. Возвращает новое значение.

    Redis-first (переживает рестарт поллера); in-memory fallback при сбое/отсутствии Redis.
    """
    if redis_client is not None:
        try:
            key = f"{_UPDATE_FAIL_KEY_PREFIX}{update_id}"
            count = int(await redis_client.incr(key))
            if count == 1:
                await redis_client.expire(key, _UPDATE_FAIL_TTL_SECONDS)
            return count
        except Exception:  # noqa: BLE001 — Redis лёг → in-memory fallback
            logger.warning("telegram_poller: Redis-счётчик попыток недоступен, in-memory fallback")
    _inmem_update_fail_counts[update_id] = _inmem_update_fail_counts.get(update_id, 0) + 1
    return _inmem_update_fail_counts[update_id]


async def _clear_update_failure(redis_client, update_id: int) -> None:
    """Сбросить счётчик попыток (update обработан или ядовитый — скипнут). Best-effort."""
    _inmem_update_fail_counts.pop(update_id, None)
    if redis_client is not None:
        try:
            await redis_client.delete(f"{_UPDATE_FAIL_KEY_PREFIX}{update_id}")
        except Exception:  # noqa: BLE001
            pass


async def _process_updates_batch(
    updates: list,
    *,
    engine,
    client,
    redis_pubsub,
    fail_redis,
    offset: int,
    meta_api_client=None,
) -> int:
    """Обработать батч updates. Возвращает новый offset (ack Telegram'у).

    MID-8 контракт:
    - update обработан успешно → сдвигаем offset за него (ack, больше не переотдаётся);
    - update упал, попыток < лимита → offset НЕ двигаем за него И прерываем батч
      (у следующих update_id выше — ack их сдвинул бы offset за упавший, потеряв его).
      Telegram переотдаст упавший + хвост на следующем poll;
    - update упал, попытки исчерпаны (ядовитый) → ERROR-лог, сдвигаем offset за него
      (скип навсегда), чтобы один битый update не заморозил всю очередь.

    updates от Telegram отсортированы по update_id по возрастанию — порядок гарантирован.
    """
    for update in updates:
        upd_id = int(update.get("update_id", 0))
        try:
            await handle_update(
                engine=engine,
                client=client,
                update=update,
                redis=redis_pubsub,
                # data-Redis для AI-чата (история/busy-guard/rate-limit) — тот же
                # клиент, что и heartbeat; meta_api_client — Marketing API tools.
                redis_client=fail_redis,
                meta_api_client=meta_api_client,
            )
        except Exception:
            attempts = await _bump_update_failure(fail_redis, upd_id)
            if attempts >= _MAX_UPDATE_ATTEMPTS:
                # Ядовитый update: падает стабильно. Скипаем, чтобы не залипнуть навсегда.
                logger.error(
                    "handle_update crashed %d раз (update_id=%d) — ЯДОВИТЫЙ, скипаю "
                    "(offset двигаю дальше). Money-кнопка под ним, если была, потеряна.",
                    attempts,
                    upd_id,
                    exc_info=True,
                )
                await _clear_update_failure(fail_redis, upd_id)
                if upd_id > offset:
                    offset = upd_id
                continue
            # Ретраибельный сбой: offset НЕ двигаем за упавший update — Telegram
            # переотдаст его (и хвост батча) на следующем poll. Прерываем батч.
            logger.warning(
                "handle_update crashed (update_id=%d, попытка %d/%d) — offset НЕ подтверждаю, "
                "update будет переобработан на следующем poll",
                upd_id,
                attempts,
                _MAX_UPDATE_ATTEMPTS,
            )
            break
        # Успех: ack этого update (сдвигаем offset) + чистим счётчик попыток.
        if upd_id > offset:
            offset = upd_id
        await _clear_update_failure(fail_redis, upd_id)
    return offset


async def heartbeat_loop(redis_client, stop: asyncio.Event) -> None:
    """Периодически пишет worker:heartbeat:telegram_poller с TTL 60s.

    Параллельный таск — не блокирует long-polling цикл.
    """
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("telegram_poller heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _build_meta_api_client():
    """Lazy MetaApiClient для AI-чата — паттерн apps/mcp_server/context.py.

    При недоступности browser-agent (gRPC) возвращает None: meta-tools в чате
    поднимут ToolError с понятным текстом, остальные инструменты работают.
    """
    grpc_host = os.environ.get("BROWSER_AGENT_GRPC_HOST", "localhost")
    try:
        grpc_port = int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051"))
    except ValueError:
        grpc_port = 50051
    try:
        from core.meta_api.client import MetaApiClient

        mc = MetaApiClient(host=grpc_host, port=grpc_port)
        await mc.start()
        logger.info(
            "MetaApiClient поднят (%s:%d) — meta-tools в AI-чате активны", grpc_host, grpc_port
        )
        return mc
    except Exception as exc:  # noqa: BLE001
        logger.warning("MetaApiClient не запустился (%s) — AI-чат без meta-tools", exc)
        return None


def _get_database_url() -> str:
    """То же что в backup_secrets.py — env + .env с fallback на POSTGRES_*."""
    env_vars: dict[str, str] = {}
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip('"').strip("'")
    for k in (
        "DATABASE_URL",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        if os.environ.get(k):
            env_vars[k] = os.environ[k]

    db_url = env_vars.get("DATABASE_URL")
    if not db_url:
        host = env_vars.get("POSTGRES_HOST", "127.0.0.1")
        port = env_vars.get("POSTGRES_PORT", "5432")
        db_name = env_vars.get("POSTGRES_DB")
        user = env_vars.get("POSTGRES_USER")
        password = env_vars.get("POSTGRES_PASSWORD", "")
        if not (db_name and user):
            raise RuntimeError("Не нашёл POSTGRES_DB+POSTGRES_USER")
        from urllib.parse import quote_plus

        db_url = f"postgresql+asyncpg://{user}:{quote_plus(password)}@{host}:{port}/{db_name}"
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return db_url


def _get_redis_url() -> str:
    """Redis URL из env или config."""
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        return redis_url
    try:
        from core.config import get_settings

        return get_settings().redis_url
    except Exception:
        return "redis://localhost:6380/0"


async def main_loop(db_url: str) -> None:
    """Основной long-polling цикл."""
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)

    # Graceful shutdown
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    http_client = httpx.AsyncClient(timeout=30.0)
    last_token: str = ""
    last_token_reload_at = 0.0
    last_heartbeat_at = 0.0
    client: TelegramBotClient | None = None

    # Redis pubsub клиент для creator-команд (/record_plan, /stop_record)
    redis_pubsub = RedisPubSub(_get_redis_url())

    # Отдельный redis-клиент для heartbeat SET (pubsub-клиент нельзя использовать для SET).
    hb_redis: redis_asyncio.Redis | None = None
    hb_task: asyncio.Task | None = None
    try:
        hb_redis = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)
        hb_task = asyncio.create_task(heartbeat_loop(hb_redis, shutdown_event))
    except Exception:
        logger.warning("telegram_poller: не удалось запустить heartbeat")

    # MetaApiClient для AI-чата (Marketing API READ tools). Best-effort: без
    # browser-agent чат работает на БД/Redis/creative/draft-инструментах.
    meta_api_client = await _build_meta_api_client()

    # offset инициализируется из БД один раз — при первом появлении токена.
    offset: int = 0
    offset_loaded = False
    idle_logged = False

    try:
        while not shutdown_event.is_set():
            now = loop.time()

            # Перечитываем config (источник bot_token). В idle (нет токена) — каждую
            # итерацию (быстрый подхват ввода через UI); при активном client — раз в N сек.
            if client is None or now - last_token_reload_at > _TOKEN_RELOAD_INTERVAL_SECONDS:
                try:
                    cfg = await load_telegram_config(engine)
                except Exception:
                    logger.exception("Не смог перечитать telegram_config")
                    cfg = None

                if cfg and cfg.bot_token:
                    if cfg.bot_token != last_token:
                        if client is None:
                            logger.info("Telegram poller: токен получен — polling активен")
                        else:
                            logger.info("Bot token изменился — пересоздаю client")
                        last_token = cfg.bot_token
                        client = TelegramBotClient(bot_token=last_token, http_client=http_client)
                        idle_logged = False
                    # offset берём из БД только при самом первом подъёме client.
                    if not offset_loaded:
                        offset = cfg.poller_offset
                        offset_loaded = True
                        logger.info("Telegram poller запущен (offset=%d)", offset)
                else:
                    # Токена нет (свежая БД / удалён в UI / не расшифровывается) — idle.
                    if client is not None or not idle_logged:
                        logger.warning(
                            "telegram_config пуст или токен не расшифровывается — "
                            "poller в режиме ожидания, введи токен в Settings (UI). "
                            "Heartbeat продолжается, запуск не падает."
                        )
                        idle_logged = True
                    client = None
                    last_token = ""
                last_token_reload_at = now

            # Heartbeat в БД (poller_status в UI) — best-effort, в т.ч. в idle.
            # Основной heartbeat для health_watchdog идёт отдельным Redis-таском.
            if now - last_heartbeat_at > _HEARTBEAT_INTERVAL_SECONDS:
                try:
                    await touch_poller_heartbeat(engine)
                except Exception:
                    logger.exception("touch_poller_heartbeat failed")
                last_heartbeat_at = now

            # Нет токена → ждём (прерываемо shutdown'ом) и пробуем снова. НЕ выходим.
            if client is None:
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(), timeout=_IDLE_RELOAD_INTERVAL_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            # Long poll
            try:
                updates = await client.get_updates(
                    offset=offset + 1 if offset > 0 else None,
                    timeout_seconds=_LONG_POLL_TIMEOUT_SECONDS,
                )
            except TelegramAPIError as exc:
                if exc.error_code == 409:
                    # MID-7: 409 Conflict — Telegram видит два одновременных getUpdates
                    # с одним токеном (второй поллер запущен параллельно, например
                    # старый деплой не остановлен). Мгновенный ретрай только усиливает
                    # драку за offset — ждём дольше и явно сигналим причину в лог.
                    logger.error(
                        "getUpdates 409 Conflict — похоже, запущен второй поллер с этим "
                        "же токеном (два процесса/деплоя одновременно?). Жду %sс перед "
                        "повтором.",
                        _CONFLICT_RETRY_DELAY_SECONDS,
                    )
                    await asyncio.sleep(_CONFLICT_RETRY_DELAY_SECONDS)
                else:
                    logger.warning("get_updates Telegram API error: %s", exc)
                    await asyncio.sleep(_ERROR_RETRY_DELAY_SECONDS)
                continue
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                logger.warning("get_updates network error: %s", exc)
                await asyncio.sleep(_ERROR_RETRY_DELAY_SECONDS)
                continue
            except Exception:
                logger.exception("get_updates unexpected error")
                await asyncio.sleep(_ERROR_RETRY_DELAY_SECONDS)
                continue

            if not updates:
                continue

            # MID-8: offset двигаем ТОЛЬКО за успешно обработанные updates. Упавший
            # (не-ядовитый) update оставляет offset позади себя → Telegram переотдаст его
            # на следующем poll (at-least-once для money-кнопок). fail_redis == hb_redis —
            # тот же клиент, что и heartbeat (INCR/EXPIRE); None → in-memory fallback.
            offset = await _process_updates_batch(
                updates,
                engine=engine,
                client=client,
                redis_pubsub=redis_pubsub,
                fail_redis=hb_redis,
                offset=offset,
                meta_api_client=meta_api_client,
            )

            # Сохраняем offset чтобы при рестарте не обрабатывать заново
            try:
                await save_poller_offset(engine, offset)
            except Exception:
                logger.exception("save_poller_offset failed")
    finally:
        logger.info("Telegram poller завершён")
        # Останавливаем heartbeat-таск.
        shutdown_event.set()
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
        if hb_redis is not None:
            try:
                await hb_redis.aclose()
            except Exception:
                pass
        try:
            await redis_pubsub.close()
        except Exception:
            pass
        if meta_api_client is not None:
            try:
                await meta_api_client.close()
            except Exception:
                pass
        try:
            await http_client.aclose()
        except Exception:
            pass
        await engine.dispose()
