# -*- coding: utf-8 -*-
"""SQL-фрагменты для корректной агрегации кумулятивных метрик `ad_metrics`.

ПРОБЛЕМА (CRIT-1, money-bug).
`ad_metrics` хранит КУМУЛЯТИВНЫЕ snapshot'ы: каждый scan-цикл (~90с) пишет
строку с текущим накопленным значением за сутки (spend/leads/deposits растут
в течение суток кабинета). Наивный `SUM(spend)` по окну складывает ВСЕ
промежуточные снимки и завышает spend во столько раз, сколько было циклов
(десятки-сотни раз). Это прямой money-bug в аналитике.

Дополнительный нюанс: spend СБРАСЫВАЕТСЯ ПОСУТОЧНО (cabinet day reset, см.
`core/cabinet_day.py::is_cabinet_day_reset_scan` — zero-scan сигнализирует
начало новых суток). Значит за многодневное окно spend растёт, обнуляется в
начале каждых суток кабинета и снова растёт.

ПРАВИЛЬНЫЙ ПАТТЕРН (канон — `core/telegram/digest_builder.py`):
- В пределах одних суток кумулятив монотонен → берём ПОСЛЕДНИЙ snapshot
  (`DISTINCT ON ... ORDER BY ... cycle_ts DESC`), он и есть дневной итог.
- Суточное окно / hour-bucket: один latest-per-ad (или latest-per-ad-per-hour)
  → затем SUM по объявлениям/бакетам.
- Многодневное окно: latest-per-ad-PER-DAY → затем SUM по всем (ad, day).
  Так корректно складываются ДНЕВНЫЕ итоги через посуточные сбросы.

Здесь — две функции, возвращающие готовый CTE-текст (SQL-фрагмент) для
встраивания в `WITH <alias> AS (...)`. Партиционный фильтр
`cycle_ts BETWEEN :from AND :to` ОБЯЗАТЕЛЕН внутри CTE — это даёт partition
pruning по `ad_metrics` (партиционирована по cycle_ts) и не сканирует
исторические партиции.

Decimal: spend/cpc/cost_per_lead — NUMERIC, не трогаем тип в SQL, на Python
стороне оборачиваем в Decimal (как в digest_builder).

Cabinet-day bucket определяется по валидированному IANA timezone из
`meta_account_snapshot`. Runtime refresh сохраняет его из Meta Graph; неизвестное
или невалидное значение явно помечается `timezone_known = false`. UTC используется
только как совместимая оценка для SQL-бакета и никогда не должен представляться
потребителем как точный итог.
"""

from __future__ import annotations

# Колонки, которые имеет смысл агрегировать как «последний snapshot за день».
# spend/impressions/clicks/leads/registrations/deposits — все кумулятивные.
_DEFAULT_METRIC_COLUMNS: tuple[str, ...] = (
    "spend",
    "impressions",
    "clicks",
    "leads",
    "registrations",
    "deposits",
)


def _columns_select(columns: tuple[str, ...], *, table_alias: str) -> str:
    """`m.spend, m.impressions, ...` для SELECT-списка DISTINCT ON."""
    return ", ".join(f"{table_alias}.{col}" for col in columns)


def latest_per_ad_window_cte(
    *,
    cte_alias: str,
    columns: tuple[str, ...] = _DEFAULT_METRIC_COLUMNS,
    from_param: str = "from_dt",
    to_param: str = "to_dt",
    extra_select: str = "",
    bucket_expr: str | None = None,
) -> str:
    """CTE: ПОСЛЕДНИЙ snapshot на объявление в окне (для суточного окна / бакета).

    Режимы:
    - `bucket_expr is None` — latest-per-ad по всему окну
      (`DISTINCT ON (m.ad_id) ... ORDER BY m.ad_id, m.cycle_ts DESC`).
      Корректно ТОЛЬКО для окна в пределах одних суток (иначе теряются
      дневные итоги до reset'а — для многодневного используй per-day).
    - `bucket_expr` задан (например `date_trunc('hour', m.cycle_ts)`) —
      latest-per-ad-PER-BUCKET: `DISTINCT ON (<bucket>, m.ad_id)`. Внутри
      hour-бакета кумулятив монотонен → последний снимок = итог за этот час.

    Параметры:
    - `cte_alias` — имя CTE (его же используют в основном SELECT).
    - `columns` — какие метрические колонки протащить в результат.
    - `from_param`/`to_param` — имена bind-параметров границ окна.
    - `extra_select` — доп. выражения для SELECT-списка CTE (например
      `, date_trunc('hour', m.cycle_ts) AS bucket_ts`); пишется ПОСЛЕ колонок.
    - `bucket_expr` — выражение бакета; добавляется первым ключом в
      DISTINCT ON и ORDER BY.

    Возвращает текст вида `<cte_alias> AS ( SELECT DISTINCT ON ... )` —
    подставляется внутрь `WITH ...`.
    """
    cols = _columns_select(columns, table_alias="m")
    if bucket_expr:
        distinct_keys = f"{bucket_expr}, m.ad_id"
        order_keys = f"{bucket_expr}, m.ad_id, m.cycle_ts DESC"
    else:
        distinct_keys = "m.ad_id"
        order_keys = "m.ad_id, m.cycle_ts DESC"

    return f"""{cte_alias} AS (
        SELECT DISTINCT ON ({distinct_keys})
            m.ad_id,
            {cols}{extra_select}
        FROM ad_metrics m
        WHERE m.cycle_ts BETWEEN :{from_param} AND :{to_param}
        ORDER BY {order_keys}
    )"""


def latest_per_ad_per_day_cte(
    *,
    cte_alias: str,
    columns: tuple[str, ...] = _DEFAULT_METRIC_COLUMNS,
    from_param: str = "from_dt",
    to_param: str = "to_dt",
    extra_select: str = "",
) -> str:
    """CTE: последний snapshot на (объявление × сутки) — для МНОГОДНЕВНЫХ окон.

    `DISTINCT ON (m.ad_id, cabinet_day_bucket)` с per-account IANA timezone.

    Каждая строка результата — дневной итог одного объявления за конкретные
    сутки. Дальнейший `SUM(...) GROUP BY ad_id` (или по офферу/кампании)
    корректно складывает дневные итоги ЧЕРЕЗ посуточные сбросы spend.

    Результат всегда содержит `day_bucket` (локальная календарная дата кабинета
    как timestamp без timezone), canonical `ad_account_id`, `cabinet_timezone` и
    `timezone_known`. `extra_select` добавляет остальные поля.
    """
    cols = _columns_select(columns, table_alias="m")
    day_expr = "date_trunc('day', timezone(COALESCE(_metric_timezone.name, 'UTC'), m.cycle_ts))"
    return f"""{cte_alias} AS (
        SELECT DISTINCT ON (m.ad_id, {day_expr})
            m.ad_id,
            {cols},
            {day_expr} AS day_bucket,
            _metric_campaign.ad_account_id AS ad_account_id,
            COALESCE(_metric_timezone.name, 'UTC') AS cabinet_timezone,
            (_metric_timezone.name IS NOT NULL) AS timezone_known{extra_select}
        FROM ad_metrics m
        JOIN fb_ads _metric_ad ON _metric_ad.id = m.ad_id
        JOIN fb_adsets _metric_adset ON _metric_adset.id = _metric_ad.adset_id
        JOIN fb_campaigns _metric_campaign
          ON _metric_campaign.id = _metric_adset.campaign_id
        LEFT JOIN meta_account_snapshot _metric_account
          ON _metric_account.account_id = _metric_campaign.ad_account_id
        LEFT JOIN LATERAL (
            SELECT timezone_name.name
            FROM pg_catalog.pg_timezone_names timezone_name
            WHERE timezone_name.name = NULLIF(_metric_account.timezone_name, '')
            LIMIT 1
        ) _metric_timezone ON TRUE
        WHERE m.cycle_ts BETWEEN :{from_param} AND :{to_param}
        ORDER BY m.ad_id, {day_expr}, m.cycle_ts DESC
    )"""


__all__ = [
    "latest_per_ad_window_cte",
    "latest_per_ad_per_day_cte",
]
