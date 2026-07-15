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
from typing import Any

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.ai_assistant.digest_summary import summarize_digest
from core.ai_assistant.pulse import build_pulse
from core.config import get_settings
from core.db import WORKER_ENGINE_KWARGS
from core.telegram import format as fmt
from core.telegram.client import TelegramAPIError, TelegramBotClient
from core.telegram.digest_builder import build_digest
from core.telegram.digest_renderer import render_digest
from core.telegram.service import Recipient, load_active_recipients, load_telegram_config

logger = logging.getLogger("digest_scheduler")

WORKER_NAME = "digest_scheduler"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

# Главный цикл — раз в минуту (как и health_watchdog).
CHECK_INTERVAL_SECONDS = int(os.environ.get("DIGEST_CHECK_INTERVAL_SEC", "60"))

# MID-11 (аудит 02.07): пауза перед перезапуском упавшего цикла (_supervised, по
# образцу apps/health_watchdog/main.py, коммит 246000c7) — раньше голый gather
# без этой обёртки: одно необработанное исключение в tick_loop гасило ВЕСЬ
# scheduler (включая heartbeat_loop) молча, до следующего рестарта процесса.
LOOP_RESTART_DELAY_SECONDS = float(os.environ.get("DIGEST_LOOP_RESTART_SEC", "5"))

# Плановое время дайджеста в UTC.
DIGEST_HOUR_UTC = int(os.environ.get("DIGEST_HOUR_UTC", "9"))
DIGEST_MIN_UTC = int(os.environ.get("DIGEST_MIN_UTC", "0"))
# Ширина окна отправки в минутах — за пределами окна пропускаем.
DIGEST_WINDOW_MINUTES = int(os.environ.get("DIGEST_WINDOW_MIN", "5"))

# Redis-флаг «уже отправили» — 26 часов с запасом перекрывает окно следующего дня.
DIGEST_SENT_TTL_SECONDS = int(os.environ.get("DIGEST_SENT_TTL_SEC", str(26 * 3600)))
DIGEST_SENT_KEY_PREFIX = "digest:sent:"

# «Пульс кабинета»: дедуп per (слот, дата) — тот же паттерн, что digest:sent.
PULSE_SENT_KEY_PREFIX = "pulse:sent:"


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


async def _send_digest_to_recipients(
    *,
    tg_client: TelegramBotClient,
    text_html: str,
    recipients: list[Recipient],
) -> tuple[int, int]:
    """Шлёт digest каждому recipient. Возвращает (sent_ok, sent_fail)."""
    ok = 0
    fail = 0
    for r in recipients:
        try:
            # Отправляем в личку (без thread_id — форум-топики убраны в волне 2)
            await tg_client.send_message(
                chat_id=str(r.chat_id),
                text=text_html,
                message_thread_id=None,
                parse_mode="HTML",
            )
            ok += 1
        except TelegramAPIError as exc:
            logger.warning("Не смог отправить digest в chat_id=%s: %s", r.chat_id, exc)
            fail += 1
        except Exception:
            logger.exception("Неожиданная ошибка отправки digest в chat_id=%s", r.chat_id)
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
    'no_tg_config' / 'no_recipients' / 'sent' / 'send_failed').

    MID-12 (аудит 02.07): sent_key ставится ТОЛЬКО если хотя бы одному получателю
    digest реально доставлен (ok > 0). Раньше флаг ставился безусловно после
    попытки рассылки — если у ВСЕХ recipients отправка упала (например TG токен
    протух в момент тика), sent-флаг всё равно вставал на 26 часов и catch-up
    (is_in_send_window) на следующих тиках того же дня уже не срабатывал —
    digest молча пропадал на сутки. 'no_recipients' — отдельная (документированная)
    ветка: пустых получателей не с кем повторять, флаг там ставится намеренно.

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

    recipients = await load_active_recipients(engine)
    if not recipients:
        logger.info("Нет активных получателей digest — флаг ставим, чтобы не долбить пустотой")
        try:
            await redis_client.set(sent_key, "1", ex=DIGEST_SENT_TTL_SECONDS, nx=True)
        except Exception:
            logger.exception("Не смог поставить %s в Redis", sent_key)
        return "no_recipients"

    payload = await build_digest(engine, day_start_utc=now)
    text_html = render_digest(payload)

    # AI-резюме — best-effort надстройка: None (выключено/AI лёг) → дайджест как раньше.
    summary = await summarize_digest(payload, redis_client=redis_client)
    if summary:
        text_html = f"{text_html}\n\n🤖 {fmt.b('Вывод ассистента')}\n{fmt.esc(summary)}"

    # Рассылаем только по личкам активных recipients (forum-топик убран в рамках волны 2)
    tg_client = tg_client_factory(cfg.bot_token)
    try:
        ok, fail = await _send_digest_to_recipients(
            tg_client=tg_client,
            text_html=text_html,
            recipients=recipients,
        )
    finally:
        try:
            await tg_client.close()
        except Exception:
            logger.exception("Ошибка закрытия TG-клиента")

    logger.info("Digest отправлен: ok=%d fail=%d из %d получателей", ok, fail, len(recipients))

    if ok == 0:
        # MID-12: 0 доставленных — флаг НЕ ставим, чтобы следующий тик в пределах
        # окна (catch-up) попробовал снова, а не молчал 26 часов.
        logger.warning(
            "Digest не доставлен НИ ОДНОМУ получателю (fail=%d) — sent-флаг не ставлю, "
            "повтор на следующем тике",
            fail,
        )
        return "send_failed"

    try:
        await redis_client.set(sent_key, "1", ex=DIGEST_SENT_TTL_SECONDS, nx=True)
    except Exception:
        logger.exception("Не смог поставить %s в Redis (digest всё равно отправлен)", sent_key)

    return "sent"


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


def pulse_sent_key(now: datetime, slot: tuple[int, int]) -> str:
    """Redis-ключ дедупа пульса per (дата UTC, слот)."""
    now_utc = now.astimezone(timezone.utc)
    return f"{PULSE_SENT_KEY_PREFIX}{now_utc.strftime('%Y-%m-%d')}:{slot[0]:02d}{slot[1]:02d}"


def _due_pulse_slot(
    now: datetime, slots: list[tuple[int, int]]
) -> tuple[tuple[int, int], datetime] | None:
    """Последний наступивший слот + начало его окна (предыдущий слот или 00:00 UTC).

    Catch-up как у дайджеста: слот «должен» до конца суток, дедуп — Redis-ключом.
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
    redis_client: redis_asyncio.Redis,
    tg_client_factory,
    now: datetime,
) -> str:
    """Один проход пульса: слот → дедуп → сигналы → (AI-)отчёт → отправка.

    Статусы: 'disabled' / 'no_slot' / 'already_sent' / 'no_tg_config' /
    'no_recipients' / 'quiet' (сигналов нет — молчим, слот закрыт) / 'sent' /
    'send_failed'.
    """
    settings = get_settings()
    if not settings.ai_pulse_enabled:
        return "disabled"

    slots = parse_pulse_slots(settings.ai_pulse_slots_utc)
    due = _due_pulse_slot(now, slots)
    if due is None:
        return "no_slot"
    slot, window_start = due

    sent_key = pulse_sent_key(now, slot)
    try:
        if await redis_client.get(sent_key) is not None:
            return "already_sent"
    except Exception:
        logger.exception("pulse: не смог прочитать %s — пропускаю прогон", sent_key)
        return "already_sent"

    cfg = await load_telegram_config(engine)
    if cfg is None or not cfg.bot_token:
        return "no_tg_config"

    recipients = await load_active_recipients(engine)
    if not recipients:
        return "no_recipients"

    text_html = await build_pulse(engine, since=window_start, now=now)
    if text_html is None:
        # Сигналов нет — слот закрываем молча (семантика «проверка в HH:MM»,
        # события после проверки покроет следующий слот).
        try:
            await redis_client.set(sent_key, "quiet", ex=DIGEST_SENT_TTL_SECONDS, nx=True)
        except Exception:
            logger.exception("pulse: не смог поставить %s", sent_key)
        return "quiet"

    tg_client = tg_client_factory(cfg.bot_token)
    try:
        ok, fail = await _send_digest_to_recipients(
            tg_client=tg_client, text_html=text_html, recipients=recipients
        )
    finally:
        try:
            await tg_client.close()
        except Exception:
            logger.exception("pulse: ошибка закрытия TG-клиента")

    if ok == 0:
        logger.warning("pulse: не доставлен ни одному получателю (fail=%d) — повтор", fail)
        return "send_failed"

    try:
        await redis_client.set(sent_key, "1", ex=DIGEST_SENT_TTL_SECONDS, nx=True)
    except Exception:
        logger.exception("pulse: не смог поставить %s (пульс уже отправлен)", sent_key)
    logger.info("pulse отправлен: ok=%d fail=%d (слот %02d:%02d)", ok, fail, slot[0], slot[1])
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
            pulse_status = await run_pulse_tick(
                engine=engine,
                redis_client=redis_client,
                tg_client_factory=tg_client_factory,
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
        # Каждый цикл под _supervised: упавший цикл перезапускается, а не гасит
        # весь воркер молча (MID-11).
        await asyncio.gather(
            _supervised("heartbeat_loop", lambda: heartbeat_loop(redis_client, stop), stop),
            _supervised(
                "tick_loop",
                lambda: tick_loop(
                    engine=engine,
                    redis_client=redis_client,
                    tg_client_factory=tg_client_factory,
                    window=window,
                    stop=stop,
                ),
                stop,
            ),
            return_exceptions=True,
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
    "PULSE_SENT_KEY_PREFIX",
    "DigestWindow",
    "_supervised",
    "digest_sent_key",
    "heartbeat_loop",
    "is_in_send_window",
    "main_loop",
    "parse_pulse_slots",
    "pulse_sent_key",
    "run_one_tick",
    "run_pulse_tick",
    "tick_loop",
]
