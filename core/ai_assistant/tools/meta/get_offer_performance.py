# -*- coding: utf-8 -*-
"""Tool get_offer_performance — агрегированная статистика по офферу."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

from clients.python_grpc.meta_api_client import MetaApiError
from core.ai_assistant.tools.base import RiskLevel, ToolError
from core.meta_api.client import MetaApiHighLevelClient
from core.meta_api.insights.fetcher import InsightsFetcher
from core.meta_api.schemas import MetaInsightsRow


class GetOfferPerformanceTool:
    """Агрегированная статистика по офферу: total_spend, total_leads, average_cpl.

    Использует InsightsFetcher.fetch_for_offer с SQLAlchemy-сессией.
    При отсутствии активной сессии БД — переходит на fetch_for_ad_account
    с фильтром по offer_code в названии кампании/объявления.
    """

    name: ClassVar[str] = "get_offer_performance"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_offer_performance",
        "description": (
            "Агрегированная статистика по офферу: total_spend, total_leads, "
            "average_cpl, лучшее/худшее объявление за период."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "offer_code": {
                    "type": "string",
                    "description": "Код оффера, например DRC_CR2",
                },
                "ad_account_id": {
                    "type": "string",
                    "description": "ID рекламного кабинета (необязательно)",
                },
                "date_preset": {"type": "string", "default": "today"},
            },
            "required": ["offer_code"],
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Получить метрики оффера через InsightsFetcher.fetch_for_offer (с БД)."""
        offer_code = str(args.get("offer_code", "")).strip()
        if not offer_code:
            raise ToolError("offer_code не указан")

        date_preset = str(args.get("date_preset", "today"))
        raw_account_id: str | None = args.get("ad_account_id")
        if raw_account_id:
            raw_account_id = raw_account_id.strip()
            if not raw_account_id.startswith("act_"):
                raw_account_id = f"act_{raw_account_id}"

        try:
            rows = await _fetch_offer_rows(offer_code, raw_account_id, date_preset)
        except MetaApiError as exc:
            raise ToolError(f"Marketing API вернул ошибку (code={exc.code}): {exc}") from exc
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Не удалось получить данные по офферу: {exc}") from exc

        if rows is None:
            # Оффер не найден в БД
            return (
                f"Оффер {offer_code!r} не найден в базе данных. "
                "Убедитесь, что оффер добавлен и кампании синхронизированы."
            )

        if not rows:
            return (
                f"Оффер {offer_code!r}: объявления найдены, но за период {date_preset} данных нет."
            )

        return _format_offer_performance(rows, offer_code, date_preset)


# ── Вспомогательные функции ──────────────────────────────────────────────────


async def _fetch_offer_rows(
    offer_code: str,
    ad_account_id: str | None,
    date_preset: str,
) -> list[MetaInsightsRow] | None:
    """Загрузить MetaInsightsRow для оффера.

    Пробует получить db-сессию через core.db; если не доступна — выполняет
    поиск через get_insights с фильтром по названию кампании/объявления.

    Возвращает None если оффер не найден в БД.
    """
    try:
        # Пробуем получить сессию через dependency
        from core.db import async_session_factory

        async with async_session_factory() as db:
            async with MetaApiHighLevelClient() as client:
                fetcher = InsightsFetcher(client)
                rows = await fetcher.fetch_for_offer(
                    db,
                    offer_code,
                    ad_account_id=ad_account_id,
                    date_preset=date_preset,
                    initiated_by="get_offer_performance_tool",
                )
                return rows
    except (ImportError, AttributeError):
        # Если db-сессия недоступна — фолбэк без БД
        pass
    except Exception as exc:
        # ValueError означает "оффер не найден"
        if "не найден" in str(exc).lower() or "not found" in str(exc).lower():
            return None
        raise

    # Фолбэк: fetch всего кабинета + фильтр по offer_code в названии
    if not ad_account_id:
        raise ToolError(
            f"ad_account_id не указан и БД-сессия недоступна. "
            f"Укажите ad_account_id для поиска по офферу {offer_code!r}."
        )

    async with MetaApiHighLevelClient() as client:
        raw_rows = await client.get_insights(
            ad_account_id,
            level="ad",
            date_preset=date_preset,
            limit=500,
        )

    # Фильтруем по вхождению offer_code в имя кампании или объявления
    from core.meta_api.adapters import parse_insights_row_from_dict

    code_upper = offer_code.upper()
    matched = []
    for raw in raw_rows:
        haystack = (raw.get("campaign_name", "") + " " + raw.get("ad_name", "")).upper()
        if code_upper in haystack:
            matched.append(parse_insights_row_from_dict(raw))
    return matched


def _format_offer_performance(
    rows: list[MetaInsightsRow],
    offer_code: str,
    date_preset: str,
) -> str:
    """Агрегировать MetaInsightsRow и вернуть читаемый отчёт."""
    total_spend = Decimal("0")
    total_leads = 0
    total_registrations = 0
    total_deposits = 0
    total_impressions = 0
    total_clicks = 0

    for r in rows:
        total_spend += r.spend
        total_leads += r.leads
        total_registrations += r.registrations
        total_deposits += r.deposits
        total_impressions += r.impressions
        total_clicks += r.clicks

    avg_cpl = (total_spend / total_leads) if total_leads > 0 else None

    # Топ-объявление (наименьший CPL при leads > 0)
    rows_with_leads = [r for r in rows if r.leads > 0]
    best: MetaInsightsRow | None = None
    worst: MetaInsightsRow | None = None
    if rows_with_leads:
        best = min(rows_with_leads, key=lambda r: r.spend / r.leads)
        worst = max(rows_with_leads, key=lambda r: r.spend / r.leads)

    lines = [
        f"Оффер {offer_code} | {date_preset} | объявлений={len(rows)}",
        f"  Spend:        {total_spend:.2f}",
        f"  Leads:        {total_leads}",
        f"  Registrations:{total_registrations}",
        f"  Deposits:     {total_deposits}",
        f"  Avg CPL:      {avg_cpl:.2f}" if avg_cpl is not None else "  Avg CPL:      n/a",
        f"  Impressions:  {total_impressions:,}",
        f"  Clicks:       {total_clicks:,}",
    ]

    if best and best.ad_name:
        best_cpl = best.spend / best.leads
        lines.append(f"  Лучшее объявление: {best.ad_name[:55]} (CPL={best_cpl:.2f})")
    if worst and worst is not best and worst.ad_name:
        worst_cpl = worst.spend / worst.leads
        lines.append(f"  Худшее объявление: {worst.ad_name[:55]} (CPL={worst_cpl:.2f})")

    return "\n".join(lines)
