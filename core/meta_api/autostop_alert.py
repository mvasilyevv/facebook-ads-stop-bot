# -*- coding: utf-8 -*-
"""Детектор отказа auto-stop и durable CRITICAL notification events.

Зачем: auto-stop (observer → meta_api_mutation pause_ad) исполняется через
page.evaluate(fetch) внутри Vision-сессии. При сетевом отказе профиля fetch к
graph.facebook.com падает ``code=-2 Failed to fetch`` → TemporaryError → молчаливый
requeue (до 72 попыток ≈ 6ч). Объявление при этом может тратить бюджет, а в TG —
тишина. Этот модуль превращает серию подряд-фейлов в ОДИН операционный CRITICAL:
«авто-стоп не исполняется — почини Vision-канал», вместо тихого ретрая.

Дизайн:
- ``is_channel_down_error`` — отделяет «канал мёртв» (Vision/сеть) от Meta-side
  (rate-limit/permission/токен): только их считаем money-сигналом outage.
- каждый подтверждённый channel-down доходит до PostgreSQL incident plane;
  advisory lock атомарно схлопывает повторные тики в одну активную generation.
- Redis не участвует ни в suppression, ни в решении о создании event.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from core.meta_api.errors import RateLimitedError, SessionUnavailableError, TemporaryError
from core.money import (
    currency_exponent,
    require_exact_currency_amount,
    validated_currency_code,
)
from core.safe_diagnostics import safe_exception_diagnostic
from core.telegram.worker_notify import (
    notify_recurring_incident,
    notify_recurring_incident_in_transaction,
)
from core.wording import minutes_ru

logger = logging.getLogger(__name__)

AUTOSTOP_CHANNEL_INCIDENT_KEY = "autostop:channel_down"


def is_channel_down_error(exc: BaseException) -> bool:
    """True, если ошибка означает «Vision-канал к Graph мёртв» (а не Meta-side).

    Канал мёртв:
    - ``SessionUnavailableError`` — сессия не готова / circuit-open / token-not-found (-1);
    - ``TemporaryError`` с ``code`` отрицательным (внутренние сигналы browser-agent:
      -2 Failed to fetch, -3 page/context loss) или ``None``
      (gRPC transport failure — browser-agent недоступен).

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


async def maybe_alert_autostop_channel_down(
    *,
    exc: BaseException,
    fb_ad_id: str,
    engine: Any,
) -> bool:
    """Open or refresh the single active auto-stop channel incident."""
    if not is_channel_down_error(exc):
        return False

    logger.error(
        "autostop_alert CRITICAL (%s; ad=%s)",
        safe_exception_diagnostic(exc),
        fb_ad_id,
    )

    try:
        return await notify_recurring_incident(
            engine,
            incident_key=AUTOSTOP_CHANNEL_INCIDENT_KEY,
            audience="all",
            event_type="autostop_channel_down",
            severity="critical",
            title="Авто-стоп не доходит до Facebook",
            # Инцидент общий для всего канала, а не для одного объявления:
            # id из конкретного сбоя ушёл бы в карточку случайной целью.
            # Он остаётся в логе выше, оператору нужен канал.
            summary="Команда «выключить объявление» не дошла до кабинета.",
            risk="Объявления продолжают тратить бюджет без стопа",
            lines=[
                "Проверь browser-agent и Vision-профиль",
                "Отключи рискованные объявления вручную в Ads Manager",
            ],
            resource_type="meta_channel",
            resource_id="auto_stop",
        )
    except Exception as notify_exc:  # noqa: BLE001
        logger.error(
            "autostop_alert: durable CRITICAL event не создан (%s)",
            safe_exception_diagnostic(notify_exc),
        )
        return False


# ─────────────── Per-ad эскалация недоставленной паузы (комплементарно channel-down) ───────────────
# Channel-down CRITICAL (выше) говорит «канал мёртв» в целом. Эта эскалация — точечная: если
# КОНКРЕТНАЯ auto-stop pause_ad висит недоставленной X минут (канал Vision не оживает сам и
# авто-исцеление не справилось), шлём «выключи ВОТ ЭТО объявление вручную» с именем и спендом —
# человек как последний рубеж получает конкретную цель, а не общий сигнал.

UNDELIVERED_INCIDENT_KEY_PREFIX = "autostop:undelivered:"
TERMINAL_UNDELIVERED_INCIDENT_KEY_PREFIX = "autostop:terminal-undelivered:"
DEFAULT_ESCALATE_AFTER_SECONDS = 10 * 60
_ESCALATE_QUERY_LIMIT = 50


def _confirmed_spend_text(value: object, currency: object) -> str | None:
    """Render spend only when both exact amount and reviewed unit are present."""

    code = validated_currency_code(currency)
    if code is None:
        return None
    try:
        exponent = currency_exponent(code)
        amount = require_exact_currency_amount(
            value,
            currency=code,
            exponent=exponent,
            field="undelivered auto-stop spend",
        )
    except (TypeError, ValueError):
        return None
    return f"{amount:.{exponent}f} {code}"


async def _find_undelivered_candidate_ids(
    engine: Any,
    *,
    requested_by: str,
    stuck_after_seconds: int,
) -> list[int]:
    """Return a bounded hint set; every row is locked and rechecked later."""
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT task.id
                    FROM task_queue AS task
                    WHERE task.task_type = 'meta_api_mutation'
                      AND task.payload->>'mutation_kind' = 'pause_ad'
                      AND task.requested_by = :rb
                      AND (
                        task.status IN ('pending', 'retrying', 'running')
                        OR (
                          task.status IN ('failed', 'cancelled')
                          AND EXISTS (
                            SELECT 1
                            FROM fb_ads AS ad
                            WHERE ad.fb_ad_id = task.payload->>'target_id'
                              AND UPPER(COALESCE(ad.delivery_status, '')) = 'ACTIVE'
                          )
                          AND NOT EXISTS (
                            SELECT 1
                            FROM incidents AS terminal_incident
                            WHERE terminal_incident.incident_key =
                                  :terminal_prefix || (task.payload->>'target_id')
                              AND terminal_incident.status IN
                                  ('open', 'acknowledged', 'executing')
                          )
                        )
                      )
                      AND task.created_at < NOW() - make_interval(secs => :secs)
                    ORDER BY
                      CASE
                        WHEN task.status IN ('failed', 'cancelled') THEN 0
                        ELSE 1
                      END,
                      task.created_at ASC
                    LIMIT :lim
                    """
                    ),
                    {
                        "rb": requested_by,
                        "secs": stuck_after_seconds,
                        "lim": _ESCALATE_QUERY_LIMIT,
                        "terminal_prefix": TERMINAL_UNDELIVERED_INCIDENT_KEY_PREFIX,
                    },
                )
            )
            .scalars()
            .all()
        )
    return [int(task_id) for task_id in rows]


async def escalate_undelivered_autostop_pauses(
    engine: Any,
    *,
    requested_by: str = "bot_auto_stop",
    stuck_after_seconds: int = DEFAULT_ESCALATE_AFTER_SECONDS,
) -> int:
    """Находит auto-stop pause_ad, не доставленные дольше stuck_after_seconds, и шлёт per-ad
    «выключи вручную» (имя + спенд). Возвращает число отправленных эскалаций.

    Every matching row refreshes a durable incident with a stable per-ad key. PostgreSQL
    collapses repeats into one active generation and one editable card. The bounded
    indexed SELECT is intentionally performed on every idle tick so no in-memory
    throttle can delay a critical event."""
    if engine is None:
        return 0

    try:
        candidate_ids = await _find_undelivered_candidate_ids(
            engine,
            requested_by=requested_by,
            stuck_after_seconds=stuck_after_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "escalate_undelivered: ошибка выборки застрявших pause_ad (%s)",
            safe_exception_diagnostic(exc),
        )
        return 0

    if not candidate_ids:
        return 0

    accepted = 0
    for task_id in candidate_ids:
        try:
            async with engine.begin() as conn:
                # Lock the task before the incident advisory key.  The fenced
                # terminalizer uses the same order, so success cannot commit
                # between this final eligibility check and incident creation.
                row = (
                    await conn.execute(
                        text(
                            """
                            SELECT t.id,
                                   t.payload->>'target_id' AS fb_ad_id,
                                   t.status,
                                   t.attempt_count,
                                   t.last_error,
                                   GREATEST(
                                       0,
                                       FLOOR(EXTRACT(EPOCH FROM (NOW() - t.created_at)) / 60)
                                   )::integer AS age_minutes,
                                   ad.ad_name,
                                   ad.delivery_status,
                                   metric.spend,
                                   metric.currency
                            FROM task_queue AS t
                            LEFT JOIN fb_ads AS ad
                              ON ad.fb_ad_id = t.payload->>'target_id'
                            LEFT JOIN LATERAL (
                                SELECT spend, currency
                                FROM ad_metrics
                                WHERE ad_id = ad.id
                                  AND cycle_ts >= NOW() - INTERVAL '7 days'
                                ORDER BY cycle_ts DESC
                                LIMIT 1
                            ) AS metric ON TRUE
                            WHERE t.id = :task_id
                              AND t.task_type = 'meta_api_mutation'
                              AND t.payload->>'mutation_kind' = 'pause_ad'
                              AND t.requested_by = :rb
                              AND (
                                t.status IN ('pending', 'retrying', 'running')
                                OR (
                                  t.status IN ('failed', 'cancelled')
                                  AND UPPER(COALESCE(ad.delivery_status, '')) = 'ACTIVE'
                                  AND NOT EXISTS (
                                    SELECT 1
                                    FROM incidents AS terminal_incident
                                    WHERE terminal_incident.incident_key =
                                          :terminal_prefix || (t.payload->>'target_id')
                                      AND terminal_incident.status IN
                                          ('open', 'acknowledged', 'executing')
                                  )
                                )
                              )
                              AND t.created_at < NOW() - make_interval(secs => :secs)
                            FOR UPDATE OF t
                            """
                        ),
                        {
                            "task_id": int(task_id),
                            "rb": requested_by,
                            "secs": stuck_after_seconds,
                            "terminal_prefix": TERMINAL_UNDELIVERED_INCIDENT_KEY_PREFIX,
                        },
                    )
                ).first()
                if row is None or not row.fb_ad_id:
                    continue
                fb_ad_id = str(row.fb_ad_id)
                minutes = int(row.age_minutes or 0)
                spend_text = _confirmed_spend_text(row.spend, row.currency)
                is_terminal = str(row.status or "").lower() in {"failed", "cancelled"}
                logger.error(
                    "escalate_undelivered: pause_ad застрял %sмин (ad=%s), state=%s",
                    minutes,
                    fb_ad_id,
                    "terminal_failure" if is_terminal else "delivery_stalled",
                )
                ad_label = str(row.ad_name) if row.ad_name else fb_ad_id
                spend_part = (
                    f"потрачено {spend_text}"
                    if spend_text is not None
                    else "расход не показан: валюта кабинета не подтверждена"
                )
                was_accepted = await notify_recurring_incident_in_transaction(
                    conn,
                    incident_key=(
                        f"{TERMINAL_UNDELIVERED_INCIDENT_KEY_PREFIX}{fb_ad_id}"
                        if is_terminal
                        else f"{UNDELIVERED_INCIDENT_KEY_PREFIX}{fb_ad_id}"
                    ),
                    audience="owners",
                    event_type=(
                        "autostop_terminal_undelivered_pause"
                        if is_terminal
                        else "autostop_undelivered_pause"
                    ),
                    severity="critical",
                    # Отказ и зависание — разные новости для оператора: в первом
                    # случае ждать уже нечего, во втором команда ещё в пути.
                    title=(
                        f"Авто-стоп не сработал: {ad_label}"
                        if is_terminal
                        else f"Авто-стоп завис: {ad_label}"
                    ),
                    summary=(
                        f"Объявление не выключено, а деньги идут · {spend_part}"
                        if is_terminal
                        else (f"Команда на выключение висит {minutes_ru(minutes)} · {spend_part}")
                    ),
                    risk="Объявление может продолжать тратить бюджет",
                    lines=(
                        # Голый ID показываем только если каталог ещё не знает имени.
                        ["Открой Ads Manager и отключи объявление вручную"]
                        if row.ad_name
                        else [
                            f"Объявление {fb_ad_id}",
                            "Открой Ads Manager и отключи объявление вручную",
                        ]
                    ),
                    resource_type="ad",
                    resource_id=fb_ad_id,
                )
                accepted += int(was_accepted)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "escalate_undelivered: не удалось отправить per-ad алерт (%s)",
                safe_exception_diagnostic(exc),
            )
    return accepted
