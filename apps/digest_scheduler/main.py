# -*- coding: utf-8 -*-
"""Digest scheduler — раз в минуту проверяет окно отправки и шлёт daily digest.

Контракт:
- Окно: ``DIGEST_HOUR_UTC:DIGEST_MIN_UTC`` и до конца суток UTC (default 09:00 UTC).
  Catch-up: если scheduler упал в 09:02, а поднялся в 12:00 — digest всё равно
  уйдёт (раз в день, Redis-ключ блокирует повтор). Лучше поздний digest чем никакой.
- Защита от повторов: Redis ``digest:sent:YYYY-MM-DD`` TTL 26 часов.
- Heartbeat: ``worker:heartbeat:digest_scheduler`` TTL 60s.
- Получатели: все active recipient'ы из ``telegram_recipients`` (revoked_at IS NULL).
- При неготовом ``telegram_config`` digest пропускается (Redis-флаг не ставится),
  чтобы при появлении токена воркер дослал отчёт.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone

import redis.asyncio as redis_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.db import WORKER_ENGINE_KWARGS
from core.telegram.client import TelegramAPIError, TelegramBotClient
from core.telegram.digest_builder import build_digest
from core.telegram.digest_renderer import render_digest
from core.telegram.service import load_telegram_config

logger = logging.getLogger("digest_scheduler")

WORKER_NAME = "digest_scheduler"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

# Главный цикл — раз в минуту (как и health_watchdog).
CHECK_INTERVAL_SECONDS = int(os.environ.get("DIGEST_CHECK_INTERVAL_SEC", "60"))

# Плановое время дайджеста в UTC.
DIGEST_HOUR_UTC = int(os.environ.get("DIGEST_HOUR_UTC", "9"))
DIGEST_MIN_UTC = int(os.environ.get("DIGEST_MIN_UTC", "0"))
# Ширина окна отправки в минутах — за пределами окна пропускаем.
DIGEST_WINDOW_MINUTES = int(os.environ.get("DIGEST_WINDOW_MIN", "5"))

# Redis-флаг «уже отправили» — 26 часов с запасом перекрывает окно следующего дня.
DIGEST_SENT_TTL_SECONDS = int(os.environ.get("DIGEST_SENT_TTL_SEC", str(26 * 3600)))
DIGEST_SENT_KEY_PREFIX = "digest:sent:"


# ====================== pure helpers ======================


@dataclass(frozen=True)
class DigestWindow:
    """Спецификация планового окна отправки digest."""

    hour: int
    minute: int
    window_minutes: int


def is_in_send_window(now: datetime, window: DigestWindow) -> bool:
    """True если now попадает в [HH:MM ; конец суток UTC).

    Catch-up семантика: окно открыто от планового времени до конца суток.
    Защита от повторов реализована Redis-ключом ``digest:sent:YYYY-MM-DD``,
    не самим окном. Если scheduler упал в 09:02 — поднявшись в 12:00,
    он всё равно отправит digest (ключа ещё нет). На следующие сутки
    Redis-ключ изменится (новая дата) и окно снова откроется.

    window.window_minutes сохранён в API только для обратной совместимости —
    реальное поведение теперь catch-up до конца суток. Hard cut-off на 23:59 UTC.
    """
    if now.tzinfo is None:
        raise ValueError("now должен быть timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    target_minutes = window.hour * 60 + window.minute
    current_minutes = now_utc.hour * 60 + now_utc.minute
    # 24*60 = 1440 — следующие сутки уже не «сегодняшний» digest.
    return target_minutes <= current_minutes < 24 * 60


def digest_sent_key(now: datetime) -> str:
    """Redis-ключ дедупа отправки за сегодняшний день (UTC)."""
    if now.tzinfo is None:
        raise ValueError("now должен быть timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    return f"{DIGEST_SENT_KEY_PREFIX}{now_utc.strftime('%Y-%m-%d')}"


# ====================== I/O helpers ======================


async def _load_active_recipients(engine: AsyncEngine) -> list[tuple[int, int | None]]:
    """Возвращает [(chat_id, thread_id_or_None), ...] для активных recipient'ов.

    thread_id здесь None — у TelegramRecipient нет per-user thread. Если в будущем
    появится поле digest_subscribed/thread — добавим фильтр и колонку сюда.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT chat_id
                    FROM telegram_recipients
                    WHERE revoked_at IS NULL
                    ORDER BY chat_id
                    """
                )
            )
        ).all()
    return [(int(r[0]), None) for r in rows]


async def _send_digest_to_recipients(
    *,
    tg_client: TelegramBotClient,
    text_html: str,
    recipients: list[tuple[int, int | None]],
) -> tuple[int, int]:
    """Шлёт digest каждому recipient. Возвращает (sent_ok, sent_fail)."""
    ok = 0
    fail = 0
    for chat_id, thread_id in recipients:
        try:
            await tg_client.send_message(
                chat_id=str(chat_id),
                text=text_html,
                message_thread_id=thread_id,
                parse_mode="HTML",
            )
            ok += 1
        except TelegramAPIError as exc:
            logger.warning("Не смог отправить digest в chat_id=%s: %s", chat_id, exc)
            fail += 1
        except Exception:
            logger.exception("Неожиданная ошибка отправки digest в chat_id=%s", chat_id)
            fail += 1
    return ok, fail


async def run_one_tick(
    *,
    engine: AsyncEngine,
    redis_client: redis_asyncio.Redis,
    tg_client_factory,
    now: datetime,
    window: DigestWindow,
) -> str:
    """Один проход: проверка окна → защита от повтора → build → render → send.

    Возвращает короткий статус ('out_of_window' / 'already_sent' /
    'no_tg_config' / 'no_recipients' / 'sent').

    tg_client_factory — callable, который возвращает (client, chat_id, thread_id).
    Вынесен в параметр для тестирования (monkeypatch отдельной фабрики).
    """
    if not is_in_send_window(now, window):
        return "out_of_window"

    sent_key = digest_sent_key(now)
    try:
        if await redis_client.get(sent_key) is not None:
            return "already_sent"
    except Exception:
        logger.exception("Не смог прочитать %s из Redis — пропускаю прогон", sent_key)
        return "already_sent"

    cfg = await load_telegram_config(engine)
    if cfg is None or not cfg.bot_token:
        logger.warning("telegram_config не настроен — digest не отправлен, флаг не ставим")
        return "no_tg_config"

    recipients = await _load_active_recipients(engine)
    if not recipients:
        logger.info("Нет активных получателей digest — флаг ставим, чтобы не долбить пустотой")
        try:
            await redis_client.set(sent_key, "1", ex=DIGEST_SENT_TTL_SECONDS, nx=True)
        except Exception:
            logger.exception("Не смог поставить %s в Redis", sent_key)
        return "no_recipients"

    payload = await build_digest(engine, day_start_utc=now)
    text_html = render_digest(payload)

    tg_client = tg_client_factory(cfg.bot_token)
    try:
        ok, fail = await _send_digest_to_recipients(
            tg_client=tg_client,
            text_html=text_html,
            recipients=recipients,
        )
        # Дополнительно — в топик дайджеста супергруппы (если настроен). Ошибка
        # отправки в группу не должна ронять рассылку по личкам.
        digest_thread = getattr(cfg, "forum_digest_thread_id", None)
        if cfg.chat_id is not None and digest_thread is not None:
            try:
                await tg_client.send_message(
                    chat_id=str(cfg.chat_id),
                    text=text_html,
                    message_thread_id=digest_thread,
                    parse_mode="HTML",
                )
            except Exception:
                logger.warning("Не смог отправить digest в топик супергруппы", exc_info=True)
    finally:
        try:
            await tg_client.close()
        except Exception:
            logger.exception("Ошибка закрытия TG-клиента")

    logger.info("Digest отправлен: ok=%d fail=%d из %d получателей", ok, fail, len(recipients))

    try:
        await redis_client.set(sent_key, "1", ex=DIGEST_SENT_TTL_SECONDS, nx=True)
    except Exception:
        logger.exception("Не смог поставить %s в Redis (digest всё равно отправлен)", sent_key)

    return "sent"


# ====================== loops ======================


async def heartbeat_loop(redis_client: redis_asyncio.Redis, stop: asyncio.Event) -> None:
    """Раз в HEARTBEAT_TTL/2 пишет heartbeat в Redis."""
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:
            logger.exception("heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def tick_loop(
    *,
    engine: AsyncEngine,
    redis_client: redis_asyncio.Redis,
    tg_client_factory,
    window: DigestWindow,
    stop: asyncio.Event,
) -> None:
    """Основной цикл — раз в минуту прогоняет run_one_tick."""
    while not stop.is_set():
        try:
            now = datetime.now(timezone.utc)
            status = await run_one_tick(
                engine=engine,
                redis_client=redis_client,
                tg_client_factory=tg_client_factory,
                now=now,
                window=window,
            )
            if status not in ("out_of_window", "already_sent"):
                logger.info("digest tick status=%s", status)
        except Exception:
            logger.exception("Ошибка в digest tick")
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


def _default_tg_factory(bot_token: str) -> TelegramBotClient:
    """Фабрика по умолчанию — реальный TelegramBotClient."""
    return TelegramBotClient(bot_token)


async def main_loop(
    database_url: str | None = None,
    *,
    tg_client_factory=_default_tg_factory,
) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)
    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)

    window = DigestWindow(
        hour=DIGEST_HOUR_UTC,
        minute=DIGEST_MIN_UTC,
        window_minutes=DIGEST_WINDOW_MINUTES,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info(
        "digest_scheduler запущен (window=%02d:%02d UTC ± %d мин, tick=%ss)",
        window.hour,
        window.minute,
        window.window_minutes,
        CHECK_INTERVAL_SECONDS,
    )
    try:
        await asyncio.gather(
            heartbeat_loop(redis_client, stop),
            tick_loop(
                engine=engine,
                redis_client=redis_client,
                tg_client_factory=tg_client_factory,
                window=window,
                stop=stop,
            ),
        )
    finally:
        try:
            await redis_client.aclose()
        except Exception:
            logger.exception("Ошибка закрытия Redis")
        await engine.dispose()
        logger.info("digest_scheduler остановлен")


__all__ = [
    "DIGEST_SENT_KEY_PREFIX",
    "DIGEST_SENT_TTL_SECONDS",
    "DigestWindow",
    "digest_sent_key",
    "heartbeat_loop",
    "is_in_send_window",
    "main_loop",
    "run_one_tick",
    "tick_loop",
]
