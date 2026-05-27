# -*- coding: utf-8 -*-
"""Доставка алертов из alert_events в Telegram.

Идемпотентно через telegram_message_refs:
- ключ дедупа = (chat_id, ad_id, incident_key=open_state_token, stream_kind=stage)
- если ref уже есть — алерт пропускается (защита от двойной отправки)
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram.client import TelegramAPIError, TelegramBotClient
from core.telegram.renderer import (
    DEFAULT_PARSE_MODE,
    AlertRenderInput,
    render_alert_text,
    render_inline_keyboard,
)
from core.telegram.service import load_telegram_config

logger = logging.getLogger(__name__)


async def dispatch_pending_alerts(
    engine: AsyncEngine,
    *,
    client: TelegramBotClient,
    scan_id: int,
) -> dict[str, int]:
    """Шлёт все alert_events созданные в этом scan'е, для которых нет message_ref.

    Returns: {'sent': N, 'skipped_duplicates': M, 'errors': K}
    """
    config = await load_telegram_config(engine)
    if config is None or config.chat_id is None:
        logger.warning("Нет chat_id в telegram_config — пропускаю dispatch")
        return {"sent": 0, "skipped_duplicates": 0, "errors": 0, "skipped_no_chat": 1}

    chat_id = config.chat_id
    thread_id_by_stage = {
        "warning": config.forum_warning_thread_id,
        "stop": config.forum_stop_thread_id,
    }

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
                    ORDER BY e.created_at
                    """
                ),
                {"sid": scan_id},
            )
        ).all()

    counters = {"sent": 0, "skipped_duplicates": 0, "errors": 0}

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

        # Idempotency check — есть ли уже ref?
        async with engine.connect() as conn:
            ref_exists = (
                await conn.execute(
                    text(
                        """
                        SELECT 1 FROM telegram_message_refs
                        WHERE chat_id = :cid AND ad_id = :aid
                          AND incident_key = :ik AND stream_kind = :sk
                          AND deleted_at IS NULL
                        LIMIT 1
                        """
                    ),
                    {
                        "cid": int(chat_id),
                        "aid": ad_id,
                        "ik": incident_key,
                        "sk": stage,
                    },
                )
            ).first()

        if ref_exists:
            counters["skipped_duplicates"] += 1
            continue

        # Render
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

        # Send
        try:
            sent = await client.send_message(
                chat_id=str(chat_id),
                text=text_msg,
                parse_mode=DEFAULT_PARSE_MODE,
                message_thread_id=thread_id_by_stage.get(str(stage)),
                reply_markup=keyboard,
            )
        except TelegramAPIError as exc:
            logger.warning("Не смог отправить alert %s: %s", event_id, exc)
            counters["errors"] += 1
            continue
        except Exception:
            logger.exception("send_message crashed for alert %s", event_id)
            counters["errors"] += 1
            continue

        message_id = int((sent or {}).get("message_id", 0))
        if message_id <= 0:
            # Telegram не вернул message_id — пропускаем сохранение ref, но не считаем ошибкой
            counters["sent"] += 1
            continue

        # INSERT message_ref для будущей дедупликации + редактирования
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        INSERT INTO telegram_message_refs
                            (chat_id, ad_id, incident_key, stream_kind, message_id, thread_id)
                        VALUES
                            (:cid, :aid, :ik, :sk, :mid, :tid)
                        ON CONFLICT (chat_id, ad_id, incident_key, stream_kind)
                        DO UPDATE SET message_id = EXCLUDED.message_id,
                                      last_edited_at = NOW(),
                                      deleted_at = NULL
                        """
                    ),
                    {
                        "cid": int(chat_id),
                        "aid": ad_id,
                        "ik": incident_key,
                        "sk": stage,
                        "mid": message_id,
                        "tid": thread_id_by_stage.get(str(stage)),
                    },
                )
        except Exception:
            logger.exception("insert telegram_message_refs failed")

        counters["sent"] += 1

    return counters


__all__ = ["dispatch_pending_alerts"]
