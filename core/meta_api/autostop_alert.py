# -*- coding: utf-8 -*-
"""Детектор «money-канал auto-stop мёртв» + CRITICAL-алерт в Telegram.

Зачем: auto-stop (observer → meta_api_mutation pause_ad) исполняется через
page.evaluate(fetch) внутри Vision-сессии. При сетевом отказе профиля fetch к
graph.facebook.com падает ``code=-2 Failed to fetch`` → TemporaryError → молчаливый
requeue (до 72 попыток ≈ 6ч). Объявление при этом может тратить бюджет, а в TG —
тишина. Этот модуль превращает серию подряд-фейлов в ОДИН операционный CRITICAL:
«авто-стоп не исполняется — почини Vision-канал», вместо тихого ретрая.

Дизайн (channel-level, не per-ad → без спама):
- ``is_channel_down_error`` — отделяет «канал мёртв» (Vision/сеть) от Meta-side
  (rate-limit/permission/токен): только их считаем money-сигналом outage.
- Redis-счётчик подряд сетевых фейлов (INCR + EXPIRE = короткое окно «подряд»);
  на пороге N ставим дедуп-ключ (SET NX) и шлём ОДИН CRITICAL.
- любой успешный auto-stop (канал ожил) сбрасывает счётчик и снимает дедуп → re-arm.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from core.meta_api.errors import RateLimitedError, SessionUnavailableError, TemporaryError

logger = logging.getLogger(__name__)

# Redis-ключи. Channel-level: один счётчик/дедуп на весь auto-stop канал.
AUTOSTOP_FAIL_COUNTER_KEY = "autostop:net_fail_count"
AUTOSTOP_ALERT_DEDUP_KEY = "autostop:alerted"

# Дефолты (worker переопределяет из env).
DEFAULT_THRESHOLD = 3
DEFAULT_WINDOW_SECONDS = 30 * 60
DEFAULT_DEDUP_TTL_SECONDS = 30 * 60


def is_channel_down_error(exc: BaseException) -> bool:
    """True, если ошибка означает «Vision-канал к Graph мёртв» (а не Meta-side).

    Канал мёртв:
    - ``SessionUnavailableError`` — сессия не готова / circuit-open / token-not-found (-1/-3);
    - ``TemporaryError`` с ``code`` отрицательным (внутренние сигналы browser-agent:
      -2 Failed to fetch) или ``None`` (gRPC UNAVAILABLE/DEADLINE — browser-agent недоступен).

    Канал жив (НЕ считаем outage'ом):
    - ``RateLimitedError`` — Meta-side throttling (Meta ответила, fetch дошёл);
    - ``TemporaryError`` с положительным Graph-кодом (Meta вернула ошибку — канал работает);
    - permanent-ошибки (токен/нет объекта/нет прав) — отдельный путь, не «канал мёртв».
    """
    if isinstance(exc, RateLimitedError):
        return False
    if isinstance(exc, SessionUnavailableError):
        return True
    if isinstance(exc, TemporaryError):
        code = getattr(exc, "code", None)
        return code is None or code < 0
    return False


async def register_autostop_failure_and_should_alert(
    redis_client: Any,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    dedup_ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS,
) -> bool:
    """INCR счётчика подряд-фейлов (+EXPIRE окна). True ⇒ надо слать CRITICAL.

    На пороге атомарно ставит дедуп (SET NX) — повторные фейлы в окне молчат.
    Возвращает True ровно один раз за окно дедупа.
    """
    try:
        count = await redis_client.incr(AUTOSTOP_FAIL_COUNTER_KEY)
        # EXPIRE на каждый incr → «скользящее» окно: пока фейлы идут подряд, счётчик жив;
        # как только поток прервался дольше окна — счётчик протух (серия закончилась).
        await redis_client.expire(AUTOSTOP_FAIL_COUNTER_KEY, window_seconds)
    except Exception:  # noqa: BLE001
        logger.exception("autostop_alert: ошибка INCR счётчика фейлов")
        return False

    if count < threshold:
        return False

    try:
        ok = await redis_client.set(AUTOSTOP_ALERT_DEDUP_KEY, "1", ex=dedup_ttl_seconds, nx=True)
    except Exception:  # noqa: BLE001
        logger.exception("autostop_alert: ошибка SET дедуп-ключа")
        return False
    return bool(ok)


async def record_autostop_success(redis_client: Any) -> None:
    """Канал ожил: сброс счётчика подряд-фейлов и снятие дедупа (re-arm)."""
    try:
        await redis_client.delete(AUTOSTOP_FAIL_COUNTER_KEY, AUTOSTOP_ALERT_DEDUP_KEY)
    except Exception:  # noqa: BLE001
        logger.exception("autostop_alert: ошибка сброса счётчика/дедупа")


def build_autostop_channel_down_alert(
    *,
    fail_count: int,
    fb_ad_id: str,
    last_error: str,
) -> str:
    """CRITICAL-текст: money-сигнал + указание чинить КАНАЛ, а не «нажми кнопку»."""
    return (
        "🛑 <b>CRITICAL: авто-стоп НЕ исполняется</b>\n"
        f"Vision-канал к graph.facebook.com мёртв — <b>{fail_count}</b> подряд сетевых "
        "фейлов pause_ad (<code>Failed to fetch</code>).\n"
        f"Последняя цель: <code>{html.escape(str(fb_ad_id))}</code>\n"
        f"Ошибка: <code>{html.escape(str(last_error)[:200])}</code>\n\n"
        "⚠️ Money: STOP-вердикты не доходят до Meta, объявления могут тратить бюджет.\n"
        "Почини канал (reconnect/restart browser_agent или Vision-профиль) "
        "или выключи объявления вручную в Ads Manager."
    )


async def maybe_alert_autostop_channel_down(
    redis_client: Any,
    *,
    exc: BaseException,
    fb_ad_id: str,
    engine: Any,
    threshold: int = DEFAULT_THRESHOLD,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    dedup_ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS,
) -> bool:
    """Если ошибка = «канал мёртв» и серия достигла порога — шлёт ОДИН CRITICAL.

    Best-effort: ошибки TG/Redis не пробрасываются (не ломаем requeue auto-stop).
    Возвращает True, если решение «алертить» принято (даже если TG-клиента нет — лог).
    Рассылка всем активным recipients (без forum-топика) через notify_recipients.
    """
    if not is_channel_down_error(exc):
        return False

    should = await register_autostop_failure_and_should_alert(
        redis_client,
        threshold=threshold,
        window_seconds=window_seconds,
        dedup_ttl_seconds=dedup_ttl_seconds,
    )
    if not should:
        return False

    # Получить текущее число фейлов для текста (best-effort).
    try:
        count_raw = await redis_client.get(AUTOSTOP_FAIL_COUNTER_KEY)
        fail_count = int(count_raw) if count_raw is not None else threshold
    except Exception:  # noqa: BLE001
        fail_count = threshold

    text = build_autostop_channel_down_alert(
        fail_count=fail_count,
        fb_ad_id=fb_ad_id,
        last_error=str(exc),
    )
    logger.error("autostop_alert CRITICAL: %s (ad=%s)", str(exc), fb_ad_id)

    # Рассылка всем recipients в личку (без forum-топика)
    from core.telegram.worker_notify import notify_recipients

    try:
        await notify_recipients(
            engine,
            redis_client,
            category="autostop_channel_down",
            text=text,
        )
    except Exception:  # noqa: BLE001
        logger.exception("autostop_alert: не удалось отправить CRITICAL всем recipients")
    return True
