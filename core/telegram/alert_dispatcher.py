# -*- coding: utf-8 -*-
"""Доставка алертов из alert_events в Telegram.

Идемпотентно через telegram_message_refs:
- ключ дедупа = (chat_id, ad_id, incident_key=open_state_token, stream_kind=stage)
- если ref уже есть — алерт пропускается (защита от двойной отправки)

После успешной отправки публикует событие в Redis-канал fb_agent:alert:created
(best-effort — ошибка publish не влияет на основной flow dispatch'а).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.pubsub import CHANNEL_ALERT_CREATED
from core.telegram.client import TelegramAPIError, TelegramBotClient
from core.telegram.renderer import (
    DEFAULT_PARSE_MODE,
    AlertRenderInput,
    render_alert_text,
    render_inline_keyboard,
)
from core.telegram.service import load_active_recipients, load_telegram_config

logger = logging.getLogger(__name__)


async def _publish_alert_created(
    redis_client: Any,
    *,
    fb_ad_id: str,
    stage: str,
    matched_rule_codes: list,
    alert_event_id: Any,
) -> None:
    """Best-effort publish в fb_agent:alert:created после успешной отправки алерта."""
    if redis_client is None:
        return
    try:
        import json
        from datetime import UTC, datetime

        payload = json.dumps(
            {
                "fb_ad_id": fb_ad_id,
                "stage": stage,
                "matched_rule_codes": list(matched_rule_codes or []),
                "alert_event_id": str(alert_event_id),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
        await redis_client.publish(CHANNEL_ALERT_CREATED, payload)
    except Exception:
        # Publish не критичен — не роняем dispatch при сбое Redis
        logger.warning("alert_dispatcher: не удалось publish в %s", CHANNEL_ALERT_CREATED)


async def _send_alert_with_fallback(
    client: TelegramBotClient,
    *,
    chat_id: str,
    text_msg: str,
    keyboard: dict | None,
    thread_id: int | None,
    event_id: Any,
) -> dict | None:
    """Шлёт алерт в форум-топик thread_id; при проблеме с топиком — fallback в General.

    Форум-топики убраны редизайном, но forum_*_thread_id остались в конфиге. Если топик
    удалён/закрыт — НЕ теряем алерт (особенно warning — ранний сигнал), а пересылаем в
    общий чат без thread_id. Возвращает ответ Telegram (dict) или None при неудаче.
    """
    try:
        return await client.send_message(
            chat_id=chat_id,
            text=text_msg,
            parse_mode=DEFAULT_PARSE_MODE,
            message_thread_id=thread_id,
            reply_markup=keyboard,
        )
    except TelegramAPIError as exc:
        desc = (exc.description or "").lower()
        thread_problem = thread_id is not None and (
            "thread" in desc or "topic" in desc or "chat not found" in desc
        )
        if not thread_problem:
            logger.warning("Не смог отправить alert %s: %s", event_id, exc)
            return None
        logger.warning(
            "alert %s: топик %s недоступен (%s) — пересылаю в General",
            event_id,
            thread_id,
            exc,
        )
        try:
            return await client.send_message(
                chat_id=chat_id,
                text=text_msg,
                parse_mode=DEFAULT_PARSE_MODE,
                message_thread_id=None,
                reply_markup=keyboard,
            )
        except Exception as exc2:  # noqa: BLE001
            logger.warning("Не смог отправить alert %s даже в General: %s", event_id, exc2)
            return None
    except Exception:
        logger.exception("send_message crashed for alert %s", event_id)
        return None


async def _deliver_one_alert(
    engine: AsyncEngine,
    *,
    client: TelegramBotClient,
    redis_client: Any,
    chat_id: int,
    thread_id_by_stage: dict[str, int | None],
    event_id: Any,
    ad_id: Any,
    stage: str,
    matched_codes: list,
    metrics_json: dict,
    open_token: Any,
    fb_ad_id: str,
    ad_name: str,
    adset_name: str,
    campaign_name: str,
    offer_code: str | None,
    incident_key: str,
    counters: dict[str, int],
) -> None:
    """Общий движок доставки одного алерта: pre-claim → send → update/rollback.

    Используется и в dispatch_pending_alerts, и в sweep_orphan_alerts (DRY).
    Мутирует counters: {'sent', 'skipped_duplicates', 'errors'}.
    """
    # Pre-claim: INSERT с sentinel message_id=0 ON CONFLICT DO NOTHING.
    # Если RETURNING пустой — кто-то уже сделал claim → skip без send'а
    # (защита от двойного TG-сообщения при параллельных dispatch'ах).
    async with engine.begin() as conn:
        claim_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_message_refs
                        (chat_id, ad_id, incident_key, stream_kind,
                         message_id, thread_id)
                    VALUES
                        (:cid, :aid, :ik, :sk, 0, :tid)
                    ON CONFLICT (chat_id, ad_id, incident_key, stream_kind)
                    DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "cid": int(chat_id),
                    "aid": ad_id,
                    "ik": incident_key,
                    "sk": stage,
                    "tid": thread_id_by_stage.get(str(stage)),
                },
            )
        ).first()

    if claim_row is None:
        counters["skipped_duplicates"] += 1
        return

    claim_id = claim_row[0]

    # Рендер сообщения и клавиатуры
    render_input = AlertRenderInput(
        fb_ad_id=str(fb_ad_id),
        ad_name=str(ad_name or ""),
        campaign_name=str(campaign_name or ""),
        adset_name=str(adset_name or ""),
        offer_code=str(offer_code) if offer_code else None,
        stage=str(stage),
        matched_rule_codes=list(matched_codes or []),
        metrics=dict(metrics_json or {}),
        open_state_token=str(open_token) if open_token else None,
    )
    text_msg = render_alert_text(render_input)
    keyboard = render_inline_keyboard(render_input)

    # Send — с fallback в General при недоступном топике (не теряем алерт).
    thread_id = thread_id_by_stage.get(str(stage))
    sent = await _send_alert_with_fallback(
        client,
        chat_id=str(chat_id),
        text_msg=text_msg,
        keyboard=keyboard,
        thread_id=thread_id,
        event_id=event_id,
    )
    send_failed = sent is None
    if send_failed:
        counters["errors"] += 1

    if send_failed:
        # Освобождаем claim, чтобы ретрай (или другой воркер) мог переслать
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM telegram_message_refs WHERE id = :i"),
                    {"i": claim_id},
                )
        except Exception:
            logger.exception("rollback telegram_message_refs claim failed")
        return

    message_id = int((sent or {}).get("message_id", 0))
    if message_id <= 0:
        # Telegram не вернул message_id — sentinel-row остаётся в БД для
        # последующей дедупликации, но без реального message_id (== 0).
        counters["sent"] += 1
        # Publish best-effort даже без реального message_id — алерт был отправлен
        await _publish_alert_created(
            redis_client,
            fb_ad_id=str(fb_ad_id),
            stage=str(stage),
            matched_rule_codes=list(matched_codes or []),
            alert_event_id=event_id,
        )
        return

    # UPDATE claim'а реальным message_id + sent_at
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE telegram_message_refs
                    SET message_id = :mid,
                        sent_at = NOW(),
                        last_edited_at = NOW(),
                        deleted_at = NULL
                    WHERE id = :i
                    """
                ),
                {"mid": message_id, "i": claim_id},
            )
    except Exception:
        logger.exception("update telegram_message_refs message_id failed")

    # Publish в Redis-канал после успешной отправки + обновления ref (best-effort)
    await _publish_alert_created(
        redis_client,
        fb_ad_id=str(fb_ad_id),
        stage=str(stage),
        matched_rule_codes=list(matched_codes or []),
        alert_event_id=event_id,
    )

    counters["sent"] += 1


async def dispatch_pending_alerts(
    engine: AsyncEngine,
    *,
    client: TelegramBotClient,
    scan_id: int,
    redis_client: Any = None,
) -> dict[str, int]:
    """Шлёт все alert_events созданные в этом scan'е, для которых нет message_ref.

    Returns: {'sent': N, 'skipped_duplicates': M, 'errors': K}
    """
    config = await load_telegram_config(engine)
    if config is None or not config.bot_token:
        logger.warning("Нет bot_token в telegram_config — пропускаю dispatch")
        return {"sent": 0, "skipped_duplicates": 0, "errors": 0, "skipped_no_config": 1}

    # Волна 2: рассылка всем активным recipients в личку (вместо одного config.chat_id).
    # Дедуп per-chat гарантирован UNIQUE(chat_id, ad_id, incident_key, stream_kind).
    recipients = await load_active_recipients(engine)
    if not recipients:
        logger.warning("dispatch: нет активных recipients — пропускаю")
        return {"sent": 0, "skipped_duplicates": 0, "errors": 0, "skipped_no_recipients": 1}

    # Топики форума не используются при DM-рассылке (всегда None → General).
    thread_id_by_stage: dict[str, int | None] = {}

    # Partition pruning: ограничиваем диапазон created_at последним часом.
    # alert_events партиционирована по RANGE(created_at) — без фильтра по
    # partition-ключу планировщик выполняет full-scan всех партиций (~365).
    # Scan-цикл всегда завершается в течение секунд/минут, поэтому 1 час —
    # достаточное окно, которое гарантированно захватит текущую партицию.
    since_dt = datetime.now(timezone.utc) - timedelta(hours=1)

    # Загружаем все события этого scan'а + связанные ad/campaign/adset/offer
    async with engine.connect() as conn:
        events = (
            await conn.execute(
                text(
                    """
                    SELECT
                        e.id, e.ad_id, e.stage, e.state,
                        e.matched_rule_codes, e.metrics_json, e.open_state_token,
                        a.fb_ad_id, a.ad_name,
                        ads.adset_name,
                        c.campaign_name,
                        o.code AS offer_code
                    FROM alert_events e
                    JOIN fb_ads a ON a.id = e.ad_id
                    JOIN fb_adsets ads ON ads.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = ads.campaign_id
                    LEFT JOIN offers o ON o.id = c.offer_id
                    WHERE e.scan_id = :sid
                      AND e.created_at >= :since
                    ORDER BY e.created_at
                    """
                ),
                {"sid": scan_id, "since": since_dt},
            )
        ).all()

    counters: dict[str, int] = {"sent": 0, "skipped_duplicates": 0, "errors": 0}

    for row in events:
        (
            event_id,
            ad_id,
            stage,
            state,
            matched_codes,
            metrics_json,
            open_token,
            fb_ad_id,
            ad_name,
            adset_name,
            campaign_name,
            offer_code,
        ) = row

        if not open_token:
            # без token нечем дедуплицировать → используем event_id как key
            incident_key = f"event-{event_id}"
        else:
            incident_key = str(open_token)

        # Рассылаем каждому активному recipient'у независимо.
        # Per-chat дедуп через UNIQUE(chat_id, ad_id, incident_key, stream_kind).
        for r in recipients:
            await _deliver_one_alert(
                engine,
                client=client,
                redis_client=redis_client,
                chat_id=int(r.chat_id),
                thread_id_by_stage=thread_id_by_stage,
                event_id=event_id,
                ad_id=ad_id,
                stage=str(stage),
                matched_codes=list(matched_codes or []),
                metrics_json=dict(metrics_json or {}),
                open_token=open_token,
                fb_ad_id=str(fb_ad_id),
                ad_name=str(ad_name or ""),
                adset_name=str(adset_name or ""),
                campaign_name=str(campaign_name or ""),
                offer_code=str(offer_code) if offer_code else None,
                incident_key=incident_key,
                counters=counters,
            )

    return counters


async def sweep_orphan_alerts(
    engine: AsyncEngine,
    *,
    client: TelegramBotClient,
    redis_client: Any = None,
    hours: int = 24,
) -> dict[str, int]:
    """Retry-sweep: ресендит alert_events за последние `hours` часов без message_ref.

    Осиротевший алерт = есть alert_event, но нет соответствующего telegram_message_refs.
    Сопоставление: ad_id + incident_key=open_state_token::text + stream_kind=stage.

    Вызывается в конце каждого scan-цикла — возвращает {'sent': N, 'skipped_duplicates': M,
    'errors': K}. Алерты с NULL open_state_token пропускаются (нет incident_key).
    """
    config = await load_telegram_config(engine)
    if config is None or not config.bot_token:
        logger.debug("sweep_orphan_alerts: нет bot_token — пропускаю")
        return {"sent": 0, "skipped_duplicates": 0, "errors": 0, "skipped_no_config": 1}

    # Волна 2: sweep по каждому recipient'у независимо.
    # NOT EXISTS фильтруется per-chat через AND r.chat_id = :cid —
    # иначе при 2+ recipients sweep решит «уже доставлено» при наличии
    # ref хотя бы для одного и не отправит остальным.
    recipients = await load_active_recipients(engine)
    if not recipients:
        logger.debug("sweep_orphan_alerts: нет активных recipients — пропускаю")
        return {"sent": 0, "skipped_duplicates": 0, "errors": 0, "skipped_no_recipients": 1}

    # Топики форума не используются при DM-рассылке.
    thread_id_by_stage: dict[str, int | None] = {}

    since_dt = datetime.now(timezone.utc) - timedelta(hours=hours)

    counters: dict[str, int] = {"sent": 0, "skipped_duplicates": 0, "errors": 0}

    for r in recipients:
        # SELECT осиротевших для данного recipient'а: alert_events за окно hours,
        # где НЕТ matching message_ref С ЭТИМ chat_id.
        # AND r.chat_id = :cid — ключевое отличие от pre-волна-2 варианта:
        # без него при наличии ref для другого recipient'а sweep пропускал доставку.
        # Фильтр created_at >= :since обеспечивает partition pruning.
        # NULL open_state_token исключаем — нет incident_key для дедупликации.
        async with engine.connect() as conn:
            orphans = (
                await conn.execute(
                    text(
                        """
                        SELECT
                            e.id, e.ad_id, e.stage, e.state,
                            e.matched_rule_codes, e.metrics_json, e.open_state_token,
                            a.fb_ad_id, a.ad_name,
                            ads.adset_name,
                            c.campaign_name,
                            o.code AS offer_code
                        FROM alert_events e
                        JOIN fb_ads a ON a.id = e.ad_id
                        JOIN fb_adsets ads ON ads.id = a.adset_id
                        JOIN fb_campaigns c ON c.id = ads.campaign_id
                        LEFT JOIN offers o ON o.id = c.offer_id
                        WHERE e.created_at >= :since
                          AND e.open_state_token IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM telegram_message_refs r
                              WHERE r.ad_id = e.ad_id
                                AND r.incident_key = e.open_state_token::text
                                AND r.stream_kind = e.stage
                                AND r.chat_id = :cid
                          )
                        ORDER BY e.created_at
                        """
                    ),
                    {"since": since_dt, "cid": int(r.chat_id)},
                )
            ).all()

        if not orphans:
            continue

        logger.info(
            "sweep_orphan_alerts: recipient %d — найдено %d осиротевших за %dч",
            r.chat_id,
            len(orphans),
            hours,
        )

        for row in orphans:
            (
                event_id,
                ad_id,
                stage,
                _state,
                matched_codes,
                metrics_json,
                open_token,
                fb_ad_id,
                ad_name,
                adset_name,
                campaign_name,
                offer_code,
            ) = row

            # open_state_token IS NOT NULL гарантирован WHERE-фильтром выше
            incident_key = str(open_token)

            await _deliver_one_alert(
                engine,
                client=client,
                redis_client=redis_client,
                chat_id=int(r.chat_id),
                thread_id_by_stage=thread_id_by_stage,
                event_id=event_id,
                ad_id=ad_id,
                stage=str(stage),
                matched_codes=list(matched_codes or []),
                metrics_json=dict(metrics_json or {}),
                open_token=open_token,
                fb_ad_id=str(fb_ad_id),
                ad_name=str(ad_name or ""),
                adset_name=str(adset_name or ""),
                campaign_name=str(campaign_name or ""),
                offer_code=str(offer_code) if offer_code else None,
                incident_key=incident_key,
                counters=counters,
            )

    if counters["sent"]:
        logger.info("sweep_orphan_alerts: отправлено %d алертов", counters["sent"])

    return counters


__all__ = ["dispatch_pending_alerts", "sweep_orphan_alerts"]
