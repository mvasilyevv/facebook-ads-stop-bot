# -*- coding: utf-8 -*-
"""Ingest входящих postback'ов от AdSet.pro в adsetpro_postback_events.

См. META_INTEGRATION_PLAN.md §4.4 / Этап 6 / Волна 3.

Логика:
1. По fb_ad_id (ext_sub6) пытаемся разрезолвить fb_ad_fk через fb_ads.fb_ad_id.
2. Проверяем существующие записи с (click_id, event_type) внутри окна дедупа
   (по умолчанию 24h) — защита от ретраев AdSet.pro, у которых каждый раз будет
   свой server-side received_at.
3. INSERT с ON CONFLICT ON CONSTRAINT uq_adsetpro_postback_dedup DO NOTHING —
   защита от двух одновременных INSERT'ов с identicheskim received_at.
4. Если оба варианта сказали "дубль" — возвращаем is_duplicate=True без второй записи
   (повторный INSERT с is_duplicate=TRUE привнёс бы шум в аналитику).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.adset_pro.schemas import PostbackEvent

logger = logging.getLogger(__name__)

# Окно дедупа по (click_id, event_type). AdSet.pro обычно ретраит постбэк в течение
# минут, но мы держим запас на сутки — false-positive дублей у нас нет, потому что
# повторный реальный FTD по тому же click_id это нонсенс.
_DEDUP_WINDOW = timedelta(hours=24)


@dataclass(slots=True, frozen=True)
class IngestResult:
    """Что произошло при ingest'е одного postback'а."""

    inserted: bool
    is_duplicate: bool
    event_id: int | None
    fb_ad_fk: uuid.UUID | None


async def ingest_postback(
    engine: AsyncEngine,
    event: PostbackEvent,
    *,
    signature_valid: bool = True,
) -> IngestResult:
    """Записать postback в adsetpro_postback_events с дедупом.

    Args:
        engine: AsyncEngine SQLAlchemy.
        event: распарсенный PostbackEvent (raw из FastAPI body уже внутри).
        signature_valid: прошёл ли check секрета на endpoint'е. Сохраняется в БД
            для аналитики «сколько неподписанных пришло» — в норме всегда True.

    Returns:
        IngestResult с результатом операции. Не бросает кроме infra-ошибок.
    """
    fb_ad_fk: uuid.UUID | None = None
    if event.fb_ad_id:
        fb_ad_fk = await _resolve_fb_ad_fk(engine, fb_ad_id=event.fb_ad_id)

    dedup_after = event.received_at - _DEDUP_WINDOW

    async with engine.begin() as conn:
        # Шаг 1: пред-INSERT проверка окна дедупа — защищает от ретраев AdSet.pro.
        existing = await conn.execute(
            text(
                """
                SELECT id FROM adsetpro_postback_events
                WHERE click_id = :click_id
                  AND event_type = :event_type
                  AND received_at >= :since
                  AND received_at <= :until
                ORDER BY received_at DESC
                LIMIT 1
                """
            ),
            {
                "click_id": event.click_id,
                "event_type": event.event_type,
                "since": dedup_after,
                "until": event.received_at,
            },
        )
        existing_row = existing.first()

        if existing_row is not None:
            logger.info(
                "adsetpro postback: дубль click_id=%s event_type=%s — пропускаем INSERT",
                event.click_id,
                event.event_type,
            )
            return IngestResult(
                inserted=False,
                is_duplicate=True,
                event_id=None,
                fb_ad_fk=fb_ad_fk,
            )

        # Шаг 2: INSERT с защитой от race по UNIQUE (двух INSERT с одинаковым received_at).
        insert_result = await conn.execute(
            text(
                """
                INSERT INTO adsetpro_postback_events
                    (received_at, click_id, fb_ad_id, fb_ad_fk, event_type,
                     revenue, currency, raw_json, signature_valid, is_duplicate)
                VALUES (:received_at, :click_id, :fb_ad_id, :fb_ad_fk, :event_type,
                        :revenue, :currency, CAST(:raw_json AS JSONB),
                        :signature_valid, FALSE)
                ON CONFLICT ON CONSTRAINT uq_adsetpro_postback_dedup DO NOTHING
                RETURNING id
                """
            ),
            {
                "received_at": event.received_at,
                "click_id": event.click_id,
                "fb_ad_id": event.fb_ad_id,
                "fb_ad_fk": fb_ad_fk,
                "event_type": event.event_type,
                "revenue": event.revenue,
                "currency": event.currency,
                "raw_json": _dumps_jsonable(event.raw),
                "signature_valid": signature_valid,
            },
        )
        inserted = insert_result.first()

    if inserted is not None:
        return IngestResult(
            inserted=True,
            is_duplicate=False,
            event_id=int(inserted[0]),
            fb_ad_fk=fb_ad_fk,
        )

    # Сюда попадаем только при гонке: два параллельных ingest успели пройти SELECT
    # одновременно и обе попытались INSERT с одинаковым received_at. UNIQUE выстрелил.
    logger.info(
        "adsetpro postback: race на UNIQUE click_id=%s event_type=%s — second writer skipped",
        event.click_id,
        event.event_type,
    )
    return IngestResult(
        inserted=False,
        is_duplicate=True,
        event_id=None,
        fb_ad_fk=fb_ad_fk,
    )


async def _resolve_fb_ad_fk(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
) -> uuid.UUID | None:
    """LOOKUP fb_ads.id по fb_ads.fb_ad_id. None если ад ещё не upsert'нут observer'ом."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT id FROM fb_ads WHERE fb_ad_id = :fid LIMIT 1"),
            {"fid": fb_ad_id},
        )
        row = result.first()
    return row[0] if row else None


def _json_default(obj: Any) -> Any:
    """JSON serializer для Decimal/datetime — пользователь может прислать их в body."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Не сериализуется в JSON: {type(obj).__name__}")


def _dumps_jsonable(payload: dict[str, Any]) -> str:
    """asyncpg ждёт строку для CAST(... AS JSONB) — сериализуем сами с Decimal-safe default."""
    return json.dumps(payload, default=_json_default)
