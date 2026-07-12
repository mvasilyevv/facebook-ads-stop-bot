# -*- coding: utf-8 -*-
"""Ingest входящих postback'ов от AdSet.pro в adsetpro_postback_events.

См. META_INTEGRATION_PLAN.md §4.4 / Этап 6 / Волна 3.

Логика:
0. pg_advisory_xact_lock по (click_id, event_type) в начале транзакции — сериализует
   конкурентный дедуп одного события. Без него UNIQUE (включает received_at с
   микросекундами → у параллельных запросов он разный) не ловит дубль, а pre-SELECT под
   READ COMMITTED не видит незакоммиченный INSERT соседа.
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

# Окно дедупа по (click_id, event_type) для ОДНОРАЗОВЫХ событий (ftd/reg/...).
# AdSet.pro обычно ретраит постбэк в течение минут, но держим запас на сутки —
# повторный реальный FTD по тому же click_id это нонсенс.
_DEDUP_WINDOW = timedelta(hours=24)

# Аудит 2026-07-12 (H-4): redep/baddep по определению ПОВТОРЯЮТСЯ по одному click_id
# (игрок делает несколько депозитов за сутки). 24ч-окно глотало реальные повторные
# депозиты → недосчёт → ложный STOP прибыльного ада. Для повторяемых событий дедуп
# защищает только от доставочных ретраев AdSet.pro (минуты), не от повторов события.
_REPEATABLE_EVENT_TYPES = frozenset({"redep", "baddep"})
_DEDUP_WINDOW_REPEATABLE = timedelta(minutes=10)

# Возможные ключи идентификатора транзакции в raw postback'а. Если AdSet.pro его
# шлёт — дедуп точнее любых окон: ретрай несёт тот же id, новый депозит — другой.
_TXN_ID_RAW_KEYS = ("transaction_id", "txn_id", "conversion_id", "postback_id")


def _txn_id_from_raw(raw: dict[str, Any] | None) -> str | None:
    """Идентификатор транзакции из raw postback'а (первый непустой из известных ключей)."""
    if not raw:
        return None
    for key in _TXN_ID_RAW_KEYS:
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def dedup_window_for(event_type: str) -> timedelta:
    """Окно дедупа для типа события: повторяемые (redep/baddep) — анти-ретрай минуты."""
    if (event_type or "").lower() in _REPEATABLE_EVENT_TYPES:
        return _DEDUP_WINDOW_REPEATABLE
    return _DEDUP_WINDOW


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

    # M-4 (аудит 2026-07-12): нормализуем регистр в ЕДИНОЙ точке ingest — контракт
    # DEPOSIT_EVENT_TYPES lowercase, матчинг в evaluator/aggregator регистро-чувствителен:
    # 'FTD'/'Ftd' от AdSet.pro молча не считался бы депозитом (недосчёт → ложный STOP).
    # Оригинал сохраняется в raw_json.
    event_type = (event.event_type or "").strip().lower()

    # H-4: широкая граница (24ч) — для partition pruning и txn-дедупа; типовое окно —
    # для оконного дедупа (у redep/baddep только анти-ретрай минуты).
    dedup_after = event.received_at - _DEDUP_WINDOW
    type_window_after = event.received_at - dedup_window_for(event_type)
    incoming_txn = _txn_id_from_raw(event.raw)

    # Ключ сериализации конкурентного дедупа == ключ pre-SELECT'а: (click_id, event_type).
    # Это то, что образует «одно событие» с точки зрения дедупа.
    lock_key = f"{event.click_id}:{event_type}"

    async with engine.begin() as conn:
        # Шаг 0: advisory-lock на транзакцию по (click_id, event_type). Закрывает дыру
        # конкурентного дедупа: UNIQUE включает received_at с микросекундами (у двух
        # параллельных запросов он разный → ON CONFLICT не срабатывает), а pre-SELECT под
        # READ COMMITTED не видит незакоммиченный INSERT соседней транзакции. Advisory-lock
        # сериализует SELECT+INSERT для одного события: второй писатель ждёт COMMIT первого,
        # затем его pre-SELECT уже видит вставленную строку → корректный is_duplicate.
        # Лок висит до конца ЭТОЙ транзакции (engine.begin) и снимается на COMMIT/ROLLBACK.
        # hashtext() может коллизировать разные ключи в один bigint — это безопасно:
        # ложная сериализация двух несвязанных событий лишь чуть замедляет, но не задваивает
        # и не теряет депозиты.
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": lock_key},
        )

        # Шаг 1: пред-INSERT проверка дедупа — защищает от ретраев AdSet.pro.
        # H-4 (аудит 2026-07-12): если в raw есть txn-id — дубль это ТА ЖЕ транзакция
        # в 24ч окне (ретрай несёт тот же id, новый депозит — другой). Без txn-id —
        # оконный дедуп: 24ч для одноразовых (ftd), минуты для повторяемых (redep).
        existing = await conn.execute(
            text(
                """
                SELECT id FROM adsetpro_postback_events
                WHERE click_id = :click_id
                  AND event_type = :event_type
                  AND received_at >= :since
                  AND received_at <= :until
                  AND (
                    CASE WHEN CAST(:txn_id AS TEXT) IS NOT NULL THEN
                        COALESCE(
                            raw_json->>'transaction_id', raw_json->>'txn_id',
                            raw_json->>'conversion_id', raw_json->>'postback_id'
                        ) = :txn_id
                    ELSE received_at >= :type_since
                    END
                  )
                ORDER BY received_at DESC
                LIMIT 1
                """
            ),
            {
                "click_id": event.click_id,
                "event_type": event_type,
                "since": dedup_after,
                "until": event.received_at,
                "txn_id": incoming_txn,
                "type_since": type_window_after,
            },
        )
        existing_row = existing.first()

        if existing_row is not None:
            logger.info(
                "adsetpro postback: дубль click_id=%s event_type=%s — пропускаем INSERT",
                event.click_id,
                event_type,
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
                "event_type": event_type,
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
        event_type,
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
