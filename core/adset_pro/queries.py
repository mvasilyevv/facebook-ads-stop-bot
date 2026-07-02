# -*- coding: utf-8 -*-
"""Read-only helpers поверх adsetpro_postback_events для observer/pipeline.

Используются в evaluator-стороне (через RuleContext.external_deposits) — закрывают
gap attribution, когда Meta Ads Manager не видит депозит, а трекер AdSet.pro его
уже зарегистрировал. См. META_INTEGRATION_PLAN.md §4.4 / Этап 6.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Какие event_type'ы считаем «депозитом» в смысле защиты от STOP.
# ftd      — first time deposit (новый депозит)
# redep    — повторный депозит
# baddep   — депозит, который потом отозвали (всё равно деньги пришли)
# Hold/reg/baddep можно расширять при изменении схемы продукта — см. ingest.
DEPOSIT_EVENT_TYPES: tuple[str, ...] = ("ftd", "redep", "baddep")

# Окно для подсчёта внешних депозитов — должно быть достаточно для closure attribution
# (FB атрибутирует кликами 24h по умолчанию, депозиты тоже обычно в этом окне).
#
# MID-3 (аудит 02.07): окно 24ч широковато для no-dep guardrail'ов в evaluator'е —
# ОДИН депозит, попавший в это окно, "защищает" объявление от всех no-dep стоп-правил
# на ВСЕ оставшиеся часы окна, даже если объявление после этого депозита реально льёт
# в минус весь остаток суток. Это осознанный трейд-офф в пользу меньшего false-positive
# (не остановить объявление, которое на самом деле конвертит с лагом), а не баг — но при
# желании ужесточить guardrail'ы окно можно сузить параметром `window=` у
# load_external_deposits/load_external_deposits_batch (например, до 2-6ч) без изменения
# сигнатуры или контракта функций.
DEFAULT_EXTERNAL_DEPOSITS_WINDOW = timedelta(hours=24)


async def load_external_deposits(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
    window: timedelta = DEFAULT_EXTERNAL_DEPOSITS_WINDOW,
    now: datetime | None = None,
    event_types: tuple[str, ...] = DEPOSIT_EVENT_TYPES,
) -> int:
    """Сколько депозитных событий прислал AdSet.pro для одного fb_ad_id за window.

    Дубликаты (is_duplicate=TRUE) не учитываем — они уже есть в основной записи.

    MID-3: window по умолчанию — 24ч (см. DEFAULT_EXTERNAL_DEPOSITS_WINDOW) — один
    депозит внутри окна глушит no-dep guardrail'ы evaluator'а на всё окно; сузить
    можно передав явный `window=`.
    """
    cutoff = (now or datetime.now(timezone.utc)) - window
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT COUNT(*) FROM adsetpro_postback_events
                WHERE fb_ad_id = :fb_ad_id
                  AND event_type = ANY(:event_types)
                  AND received_at >= :since
                  AND is_duplicate = FALSE
                """
            ),
            {
                "fb_ad_id": fb_ad_id,
                "event_types": list(event_types),
                "since": cutoff,
            },
        )
        return int(result.scalar() or 0)


async def load_external_deposits_batch(
    engine: AsyncEngine,
    *,
    fb_ad_ids: list[str],
    window: timedelta = DEFAULT_EXTERNAL_DEPOSITS_WINDOW,
    now: datetime | None = None,
    event_types: tuple[str, ...] = DEPOSIT_EVENT_TYPES,
) -> dict[str, int]:
    """Batch-версия: возвращает {fb_ad_id: count} одним SQL для всего scan-цикла.

    Если для какого-то fb_ad_id нет постбэков — в результате этого ключа не будет
    (потребитель должен использовать .get(fb_ad_id, 0)).

    MID-3: window по умолчанию — 24ч (см. DEFAULT_EXTERNAL_DEPOSITS_WINDOW) — один
    депозит внутри окна глушит no-dep guardrail'ы evaluator'а на всё окно; сузить
    можно передав явный `window=`.
    """
    if not fb_ad_ids:
        return {}
    cutoff = (now or datetime.now(timezone.utc)) - window
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT fb_ad_id, COUNT(*)
                FROM adsetpro_postback_events
                WHERE fb_ad_id = ANY(:fb_ad_ids)
                  AND event_type = ANY(:event_types)
                  AND received_at >= :since
                  AND is_duplicate = FALSE
                GROUP BY fb_ad_id
                """
            ),
            {
                "fb_ad_ids": fb_ad_ids,
                "event_types": list(event_types),
                "since": cutoff,
            },
        )
        return {row[0]: int(row[1]) for row in result.all()}
