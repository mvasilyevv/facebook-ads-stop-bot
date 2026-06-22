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
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

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


# ─────────────── Per-ad эскалация недоставленной паузы (комплементарно channel-down) ───────────────
# Channel-down CRITICAL (выше) говорит «канал мёртв» в целом. Эта эскалация — точечная: если
# КОНКРЕТНАЯ auto-stop pause_ad висит недоставленной X минут (канал Vision не оживает сам и
# авто-исцеление не справилось), шлём «выключи ВОТ ЭТО объявление вручную» с именем и спендом —
# человек как последний рубеж получает конкретную цель, а не общий сигнал.

UNDELIVERED_ESCALATE_DEDUP_PREFIX = "autostop:undelivered:"
UNDELIVERED_SCAN_THROTTLE_KEY = "autostop:undelivered:scan_lock"
DEFAULT_ESCALATE_AFTER_SECONDS = 10 * 60
DEFAULT_ESCALATE_DEDUP_TTL_SECONDS = 60 * 60
DEFAULT_ESCALATE_THROTTLE_SECONDS = 60
_ESCALATE_QUERY_LIMIT = 50


def build_undelivered_pause_alert(
    *,
    ad_name: str | None,
    fb_ad_id: str,
    spend: Any,
    minutes_stuck: int,
    last_error: str | None,
) -> str:
    """Per-ad алерт «выключи вручную»: имя объявления, спенд, сколько висит, последняя ошибка."""
    name = html.escape(ad_name or "—")
    spend_txt = f"${spend}" if spend is not None else "—"
    return (
        "⚠️ <b>Авто-стоп завис — выключи вручную</b>\n"
        f"Объявление «<b>{name}</b>» (<code>{html.escape(str(fb_ad_id))}</code>) не удалось "
        f"остановить через API уже <b>{minutes_stuck}</b> мин.\n"
        f"Текущий спенд: <b>{html.escape(spend_txt)}</b>.\n\n"
        "Vision-канал недоступен → открой Ads Manager и выключи это объявление вручную.\n"
        f"Ошибка: <code>{html.escape(str(last_error or '')[:200])}</code>"
    )


def _minutes_since(created_at: Any) -> int:
    """Сколько целых минут прошло с created_at (tz-aware). 0 при ошибке/пусто."""
    if created_at is None:
        return 0
    try:
        delta = datetime.now(timezone.utc) - created_at
        return max(0, int(delta.total_seconds() // 60))
    except Exception:  # noqa: BLE001
        return 0


async def _fetch_ad_name_and_spend(engine: Any, fb_ad_id: str) -> tuple[str | None, Any]:
    """ad_name + текущий спенд по fb_ad_id. Спенд — latest-per-ad (НЕ SUM — метрики кумулятивны)."""
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT a.ad_name AS ad_name, m.spend AS spend
                        FROM fb_ads a
                        LEFT JOIN LATERAL (
                            SELECT spend FROM ad_metrics
                            WHERE ad_id = a.id AND cycle_ts >= NOW() - INTERVAL '7 days'
                            ORDER BY cycle_ts DESC
                            LIMIT 1
                        ) m ON TRUE
                        WHERE a.fb_ad_id = :fid
                        LIMIT 1
                        """
                    ),
                    {"fid": fb_ad_id},
                )
            ).first()
        if row is None:
            return None, None
        return row.ad_name, row.spend
    except Exception:  # noqa: BLE001
        logger.exception("escalate_undelivered: ошибка чтения ad_name/spend")
        return None, None


async def escalate_undelivered_autostop_pauses(
    engine: Any,
    redis_client: Any,
    *,
    requested_by: str = "bot_auto_stop",
    stuck_after_seconds: int = DEFAULT_ESCALATE_AFTER_SECONDS,
    dedup_ttl_seconds: int = DEFAULT_ESCALATE_DEDUP_TTL_SECONDS,
    throttle_seconds: int = DEFAULT_ESCALATE_THROTTLE_SECONDS,
) -> int:
    """Находит auto-stop pause_ad, не доставленные дольше stuck_after_seconds, и шлёт per-ad
    «выключи вручную» (имя + спенд). Возвращает число отправленных эскалаций.

    Дедуп per-ad (SET NX) — один алерт на объявление в окне. Скан троттлится Redis-локом
    (throttle_seconds), т.к. зовётся на каждом idle-тике воркера (~5с) — без него был бы
    SELECT каждые 5с. Best-effort: ошибки не пробрасываются (не ломаем idle-цикл)."""
    if redis_client is None or engine is None:
        return 0

    # Троттл: одно сканирование на throttle_seconds (idle-тик частый, БД не насилуем).
    if throttle_seconds > 0:
        try:
            got = await redis_client.set(
                UNDELIVERED_SCAN_THROTTLE_KEY, "1", ex=throttle_seconds, nx=True
            )
            if not got:
                return 0
        except Exception:  # noqa: BLE001
            logger.debug("escalate_undelivered: троттл-лок недоступен", exc_info=True)

    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT id, payload->>'target_id' AS fb_ad_id,
                               created_at, attempt_count, last_error
                        FROM task_queue
                        WHERE task_type = 'meta_api_mutation'
                          AND payload->>'mutation_kind' = 'pause_ad'
                          AND requested_by = :rb
                          AND status IN ('pending', 'retrying', 'running')
                          AND created_at < NOW() - make_interval(secs => :secs)
                        ORDER BY created_at ASC
                        LIMIT :lim
                        """
                    ),
                    {"rb": requested_by, "secs": stuck_after_seconds, "lim": _ESCALATE_QUERY_LIMIT},
                )
            ).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("escalate_undelivered: ошибка выборки застрявших pause_ad")
        return 0

    if not rows:
        return 0

    from core.telegram.worker_notify import notify_owners

    sent = 0
    for r in rows:
        fb_ad_id = r.fb_ad_id
        if not fb_ad_id:
            continue
        # Дедуп per-ad: один «выключи вручную» на объявление в окне dedup_ttl_seconds.
        dedup_key = f"{UNDELIVERED_ESCALATE_DEDUP_PREFIX}{fb_ad_id}"
        try:
            gate = await redis_client.set(dedup_key, "1", ex=dedup_ttl_seconds, nx=True)
        except Exception:  # noqa: BLE001
            logger.exception("escalate_undelivered: ошибка SET per-ad дедупа")
            continue
        if not gate:
            continue

        ad_name, spend = await _fetch_ad_name_and_spend(engine, fb_ad_id)
        minutes = _minutes_since(r.created_at)
        alert_text = build_undelivered_pause_alert(
            ad_name=ad_name,
            fb_ad_id=fb_ad_id,
            spend=spend,
            minutes_stuck=minutes,
            last_error=r.last_error,
        )
        logger.error(
            "escalate_undelivered: pause_ad застрял %sмин (ad=%s) — per-ad алерт", minutes, fb_ad_id
        )
        try:
            await notify_owners(
                engine,
                redis_client,
                category="autostop_undelivered_pause",
                text=alert_text,
            )
            sent += 1
        except Exception:  # noqa: BLE001
            logger.exception("escalate_undelivered: не удалось отправить per-ad алерт")
    return sent
