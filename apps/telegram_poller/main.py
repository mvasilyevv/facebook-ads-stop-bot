# -*- coding: utf-8 -*-
"""Telegram poller — минимальный long-polling loop под новую схему БД.

Запускается через run_telegram_poller.py. Перезагружает bot_token из БД
каждые 30 сек (на случай ротации). Каждые ~3 сек делает heartbeat в telegram_config.

MetaApiClient (если browser-agent доступен) поднимается один раз на процесс
и пробрасывается в `/ask` — иначе meta READ_ONLY tools падают `ToolError`,
LLM получает ошибку и формулирует ответ без них.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import create_async_engine

from core.meta_api.client import MetaApiClient
from core.pubsub import RedisPubSub
from core.telegram.bot_handler import handle_update
from core.telegram.client import TelegramBotClient
from core.telegram.service import (
    load_telegram_config,
    save_poller_offset,
    touch_poller_heartbeat,
)

logger = logging.getLogger(__name__)


_HEARTBEAT_INTERVAL_SECONDS = 30
_TOKEN_RELOAD_INTERVAL_SECONDS = 60
_ERROR_RETRY_DELAY_SECONDS = 3
_LONG_POLL_TIMEOUT_SECONDS = 25


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


async def _build_meta_api_client() -> MetaApiClient | None:
    """Поднять MetaApiClient если browser-agent доступен.

    При неудаче (ImportError / GRPC error) — None, чтобы /ask продолжал работать
    с meta-tools, возвращающими ToolError «Marketing API недоступен».
    """
    grpc_host = os.environ.get("BROWSER_AGENT_GRPC_HOST", "localhost")
    try:
        grpc_port = int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051"))
    except ValueError:
        grpc_port = 50051
    try:
        client = MetaApiClient(host=grpc_host, port=grpc_port)
        await client.start()
        logger.info(
            "MetaApiClient поднят (%s:%d) — meta-tools в /ask активны", grpc_host, grpc_port
        )
        return client
    except Exception as exc:
        logger.warning(
            "MetaApiClient не запустился (%s) — /ask продолжит работать без meta-tools", exc
        )
        return None


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
    engine = create_async_engine(db_url, echo=False)

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
    meta_api_client: MetaApiClient | None = None

    # Redis pubsub клиент для creator-команд (/record_plan, /stop_record)
    redis_pubsub = RedisPubSub(_get_redis_url())

    try:
        # Начальная загрузка config
        cfg = await load_telegram_config(engine)
        if not cfg:
            logger.error(
                "telegram_config пустой или токен не расшифровывается. "
                "Восстанови через scripts/restore_secrets.py или UI."
            )
            return

        last_token = cfg.bot_token
        client = TelegramBotClient(bot_token=last_token, http_client=http_client)
        offset = cfg.poller_offset
        logger.info("Telegram poller v2 запущен (offset=%d)", offset)

        # Один MetaApiClient на процесс. Если browser-agent оффлайн — продолжаем без него.
        meta_api_client = await _build_meta_api_client()

        while not shutdown_event.is_set():
            now = loop.time()

            # Перезагрузка токена раз в N секунд (на случай ротации в UI)
            if now - last_token_reload_at > _TOKEN_RELOAD_INTERVAL_SECONDS:
                try:
                    cfg = await load_telegram_config(engine)
                    if cfg and cfg.bot_token and cfg.bot_token != last_token:
                        logger.info("Bot token изменился — пересоздаю client")
                        last_token = cfg.bot_token
                        client = TelegramBotClient(bot_token=last_token, http_client=http_client)
                except Exception:
                    logger.exception("Не смог перечитать telegram_config")
                last_token_reload_at = now

            # Heartbeat
            if now - last_heartbeat_at > _HEARTBEAT_INTERVAL_SECONDS:
                try:
                    await touch_poller_heartbeat(engine)
                except Exception:
                    logger.exception("touch_poller_heartbeat failed")
                last_heartbeat_at = now

            # Long poll
            try:
                updates = await client.get_updates(
                    offset=offset + 1 if offset > 0 else None,
                    timeout_seconds=_LONG_POLL_TIMEOUT_SECONDS,
                )
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

            for update in updates:
                upd_id = int(update.get("update_id", 0))
                if upd_id > offset:
                    offset = upd_id
                try:
                    await handle_update(
                        engine=engine,
                        client=client,
                        update=update,
                        meta_api_client=meta_api_client,
                        redis=redis_pubsub,
                    )
                except Exception:
                    logger.exception("handle_update crashed (update_id=%d)", upd_id)

            # Сохраняем offset чтобы при рестарте не обрабатывать заново
            try:
                await save_poller_offset(engine, offset)
            except Exception:
                logger.exception("save_poller_offset failed")
    finally:
        logger.info("Telegram poller v2 завершён")
        if meta_api_client is not None:
            try:
                await meta_api_client.close()
            except Exception:
                logger.exception("MetaApiClient.close failed")
        try:
            await redis_pubsub.close()
        except Exception:
            pass
        try:
            await http_client.aclose()
        except Exception:
            pass
        await engine.dispose()
