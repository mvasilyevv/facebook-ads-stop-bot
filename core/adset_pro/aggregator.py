# -*- coding: utf-8 -*-
"""Агрегация adsetpro_postback_events → tracker_aggregate per (ad_id, country, day).

См. META_INTEGRATION_PLAN.md §5 Волна 4 / Этап 6.

Семантика (money-critical — депозиты влияют на стоп-решения через external_deposits):
- Источник — partitioned `adsetpro_postback_events` (фильтр ОБЯЗАТЕЛЬНО по received_at,
  партиционному ключу → partition pruning).
- Назначение — `tracker_aggregate` (UNIQUE(ad_id, country, day)).
- **Rebuild-паттерн (absolute recompute)**: для каждого UTC-дня, который перекрывает
  окно прогона, мы ПЕРЕСЧИТЫВАЕМ абсолютные суточные суммы из ВСЕХ не-дублей этого дня
  и пишем их через ON CONFLICT DO UPDATE SET = пересчитанные значения (не += инкремент).
  Поэтому повторный/перекрывающийся прогон НЕ задваивает — значения сходятся к истине.
  Это сознательный выбор против naive-SUM инкремента (см. урок Round 10/11).

Маппинг event_type → счётчик:
- deposits      — DEPOSIT_EVENT_TYPES (переиспользуем из core.adset_pro.queries — единый
                  источник правды с evaluator'ом, чтобы агрегат не противоречил стоп-логике).
- registrations — REGISTRATION_EVENT_TYPES.
- installs      — INSTALL_EVENT_TYPES.
Списки попарно непересекающиеся → одно событие не считается в двух счётчиках.

Что НЕ агрегируется (сознательно):
- события с fb_ad_fk IS NULL (ад ещё не upsert'нут observer'ом) — tracker_aggregate.ad_id
  NOT NULL, привязать не к чему;
- is_duplicate = TRUE — это шум дедупа;
- строки без валидного ISO-2 country в raw_json — country в агрегате NOT NULL String(2).
roi_percent НЕ вычисляется здесь: спенд живёт в ad_metrics (без разреза по country),
ROI per (ad, country, day) из этого источника корректно не посчитать — оставляем NULL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.adset_pro.queries import DEPOSIT_EVENT_TYPES

logger = logging.getLogger(__name__)

# event_type → счётчик. Списки непересекающиеся (см. assert ниже / unit-тест).
# deposits переиспользует DEPOSIT_EVENT_TYPES — единый контракт с evaluator'ом.
REGISTRATION_EVENT_TYPES: tuple[str, ...] = ("reg", "registration")
INSTALL_EVENT_TYPES: tuple[str, ...] = ("install", "installs")

# Защита-на-старте от случайного пересечения списков (один event_type в двух счётчиках
# исказил бы деньги). Падаем при импорте, а не молча даём кривые цифры.
assert not (set(DEPOSIT_EVENT_TYPES) & set(REGISTRATION_EVENT_TYPES)), "deposit/reg overlap"
assert not (set(DEPOSIT_EVENT_TYPES) & set(INSTALL_EVENT_TYPES)), "deposit/install overlap"
assert not (set(REGISTRATION_EVENT_TYPES) & set(INSTALL_EVENT_TYPES)), "reg/install overlap"


@dataclass(slots=True, frozen=True)
class AggregationResult:
    """Итог одного прогона агрегации."""

    window_start: datetime
    window_end: datetime
    day_floor: datetime
    day_ceil: datetime
    rows_upserted: int
    rows_inserted: int
    rows_updated: int
    deposits_total: int
    revenue_total: Decimal
    # Постбэки с невалидным/отсутствующим country в этом окне. M-8 (аудит 2026-07-12):
    # больше НЕ дропаются (их deposits/revenue терялись из аналитики) — бакетятся в
    # sentinel-строку country='XX'. Поле оставлено для наблюдаемости (сколько ушло в XX):
    # видно в логе прогона и в system_config.tracker_aggregator_runs.
    rows_dropped_invalid_country: int = 0


def _utc_day_bounds(window_start: datetime, window_end: datetime) -> tuple[datetime, datetime]:
    """Границы UTC-дней, перекрытых окном [window_start, window_end].

    Возвращает [day_floor, day_ceil): полночь UTC дня начала и полночь UTC дня,
    следующего за днём конца. Так пересчитываются ЦЕЛЫЕ дни (а не хвост окна) —
    это и даёт идемпотентность absolute-recompute.
    """
    start = window_start.astimezone(timezone.utc)
    end = window_end.astimezone(timezone.utc)
    if end < start:
        raise ValueError(f"window_end ({end}) < window_start ({start})")
    day_floor = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)
    day_ceil = end_day + timedelta(days=1)
    return day_floor, day_ceil


# Единый SQL: пересчёт абсолютных суточных сумм по дням [day_floor, day_ceil) и UPSERT.
# RETURNING (xmax = 0) различает INSERT (true) и UPDATE (false) — для метрик прогона.
_AGGREGATE_SQL = text(
    """
    WITH normalized AS (
        SELECT
            fb_ad_fk AS ad_id,
            UPPER(COALESCE(
                raw_json->>'country',
                raw_json->>'country_code',
                raw_json->>'geo'
            )) AS country,
            (received_at AT TIME ZONE 'UTC')::date AS day,
            event_type,
            COALESCE(revenue, 0) AS revenue,
            received_at
        FROM adsetpro_postback_events
        WHERE received_at >= :day_floor
          AND received_at < :day_ceil
          AND fb_ad_fk IS NOT NULL
          AND is_duplicate = FALSE
    ),
    filtered AS (
        -- M-8 (аудит 2026-07-12): события без валидного ISO-2 country НЕ дропаем —
        -- иначе их deposits/revenue исчезали из аналитики трекера. Кладём в sentinel
        -- 'XX' (агрегат-строка «страна неизвестна»), деньги сохраняются.
        SELECT
            ad_id,
            CASE
                WHEN country IS NOT NULL AND char_length(country) = 2 THEN country
                ELSE 'XX'
            END AS country,
            day, event_type, revenue, received_at
        FROM normalized
    )
    INSERT INTO tracker_aggregate
        (id, ad_id, country, day,
         installs, registrations, deposits, revenue,
         last_postback_at, created_at, updated_at)
    SELECT
        gen_random_uuid(),
        ad_id, country, day,
        COUNT(*) FILTER (WHERE event_type = ANY(:install_types)),
        COUNT(*) FILTER (WHERE event_type = ANY(:reg_types)),
        COUNT(*) FILTER (WHERE event_type = ANY(:deposit_types)),
        COALESCE(SUM(revenue), 0),
        MAX(received_at),
        now(), now()
    FROM filtered
    GROUP BY ad_id, country, day
    ON CONFLICT ON CONSTRAINT uq_tracker_aggregate_ad_country_day DO UPDATE SET
        installs = EXCLUDED.installs,
        registrations = EXCLUDED.registrations,
        deposits = EXCLUDED.deposits,
        revenue = EXCLUDED.revenue,
        last_postback_at = EXCLUDED.last_postback_at,
        updated_at = now()
    RETURNING (xmax = 0) AS inserted, deposits, revenue
    """
)

# Счётчик строк без валидного country. M-8 (аудит 2026-07-12): они больше не
# дропаются, а идут в sentinel country='XX' — счётчик остаётся сигналом «AdSet.pro
# сменил формат raw_json» (например все постбэки внезапно в XX). NULL fb_ad_fk и
# is_duplicate по-прежнему исключены (задокументированные намеренные исключения).
_INVALID_COUNTRY_COUNT_SQL = text(
    """
    SELECT COUNT(*)
    FROM adsetpro_postback_events
    WHERE received_at >= :day_floor
      AND received_at < :day_ceil
      AND fb_ad_fk IS NOT NULL
      AND is_duplicate = FALSE
      AND (
          UPPER(COALESCE(
              raw_json->>'country',
              raw_json->>'country_code',
              raw_json->>'geo'
          )) IS NULL
          OR char_length(UPPER(COALESCE(
              raw_json->>'country',
              raw_json->>'country_code',
              raw_json->>'geo'
          ))) <> 2
      )
    """
)


async def aggregate_postback_events(
    engine: AsyncEngine,
    *,
    window_start: datetime,
    window_end: datetime,
    deposit_event_types: tuple[str, ...] = DEPOSIT_EVENT_TYPES,
    registration_event_types: tuple[str, ...] = REGISTRATION_EVENT_TYPES,
    install_event_types: tuple[str, ...] = INSTALL_EVENT_TYPES,
) -> AggregationResult:
    """Пересчитать tracker_aggregate за UTC-дни, перекрытые окном [window_start, window_end].

    Идемпотентно: повторный прогон с тем же/перекрывающимся окном даёт те же значения.
    Возвращает AggregationResult с метриками прогона (для лога/аудита).
    """
    day_floor, day_ceil = _utc_day_bounds(window_start, window_end)

    day_bounds_params = {"day_floor": day_floor, "day_ceil": day_ceil}

    async with engine.begin() as conn:
        result = await conn.execute(
            _AGGREGATE_SQL,
            {
                **day_bounds_params,
                "deposit_types": list(deposit_event_types),
                "reg_types": list(registration_event_types),
                "install_types": list(install_event_types),
            },
        )
        rows = result.all()

        # LOW (аудит 02.07): отдельный лёгкий COUNT в той же транзакции — та же
        # видимость данных, что и основной upsert (consistent read).
        dropped_invalid_country = int(
            (await conn.execute(_INVALID_COUNTRY_COUNT_SQL, day_bounds_params)).scalar() or 0
        )

    inserted = sum(1 for r in rows if r[0])
    deposits_total = sum(int(r[1] or 0) for r in rows)
    revenue_total = sum((Decimal(r[2] or 0) for r in rows), Decimal(0))

    agg = AggregationResult(
        window_start=window_start,
        window_end=window_end,
        day_floor=day_floor,
        day_ceil=day_ceil,
        rows_upserted=len(rows),
        rows_inserted=inserted,
        rows_updated=len(rows) - inserted,
        deposits_total=deposits_total,
        revenue_total=revenue_total,
        rows_dropped_invalid_country=dropped_invalid_country,
    )
    logger.info(
        "tracker_aggregate: дни [%s..%s) → upsert=%d (new=%d upd=%d) deposits=%d revenue=%s "
        "dropped_invalid_country=%d",
        day_floor.date(),
        day_ceil.date(),
        agg.rows_upserted,
        agg.rows_inserted,
        agg.rows_updated,
        agg.deposits_total,
        agg.revenue_total,
        agg.rows_dropped_invalid_country,
    )
    if agg.rows_dropped_invalid_country > 0:
        logger.warning(
            "tracker_aggregate: %d постбэков дня [%s..%s) без валидного country → "
            "sentinel 'XX' (деньги сохранены) — проверь формат постбэков AdSet.pro",
            agg.rows_dropped_invalid_country,
            day_floor.date(),
            day_ceil.date(),
        )
    return agg
