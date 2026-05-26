# -*- coding: utf-8 -*-
"""Tool find_ads — поиск объявлений по фильтру через Marketing API."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

from clients.python_grpc.meta_api_client import MetaApiError
from core.ai_assistant.tools.base import RiskLevel, ToolError
from core.meta_api.client import MetaApiHighLevelClient


class FindAdsTool:
    """Найти объявления по фильтру: spend > X, cpl > X, status, offer_code.

    Вызывает get_insights для всего кабинета, затем фильтрует в Python
    (Marketing API /insights не поддерживает все эти фильтры нативно).
    """

    name: ClassVar[str] = "find_ads"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "find_ads",
        "description": (
            "Найти объявления по фильтру: spend > X, cpl > X, status, offer_code. "
            "Возвращает топ N с метриками."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string"},
                "filter": {
                    "type": "object",
                    "properties": {
                        "spend_gt": {"type": "number"},
                        "spend_lt": {"type": "number"},
                        "cpl_gt": {"type": "number"},
                        "cpl_lt": {"type": "number"},
                        "offer_code": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["ACTIVE", "PAUSED", "any"],
                        },
                    },
                },
                "date_preset": {"type": "string", "default": "today"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["ad_account_id"],
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Получить все объявления кабинета и вернуть отфильтрованный топ."""
        # Нормализуем ad_account_id
        raw_account_id = str(args.get("ad_account_id", "")).strip()
        if not raw_account_id:
            raise ToolError("ad_account_id не указан")
        if not raw_account_id.startswith("act_"):
            raw_account_id = f"act_{raw_account_id}"

        date_preset = str(args.get("date_preset", "today"))
        limit = int(args.get("limit", 10))
        filter_dict: dict[str, Any] = args.get("filter") or {}

        try:
            async with MetaApiHighLevelClient() as client:
                # Берём максимум — фильтруем сами в Python
                rows = await client.get_insights(
                    raw_account_id,
                    level="ad",
                    date_preset=date_preset,
                    limit=500,
                )
        except MetaApiError as exc:
            raise ToolError(f"Marketing API вернул ошибку (code={exc.code}): {exc}") from exc
        except Exception as exc:
            raise ToolError(f"Не удалось получить данные: {exc}") from exc

        if not rows:
            return f"find_ads: для {raw_account_id} за {date_preset} нет данных."

        # Обогащаем и фильтруем
        filtered = _filter_rows(rows, filter_dict)

        # Сортируем по spend убыванием
        filtered.sort(key=lambda r: r["spend"], reverse=True)
        top_n = filtered[:limit]

        return _format_find_ads_result(top_n, raw_account_id, date_preset, filter_dict)


# ── Вспомогательные функции ──────────────────────────────────────────────────


def _extract_actions(row: dict, action_type: str) -> int:
    """Извлечь значение action_type из поля actions."""
    for action in row.get("actions", []):
        if action.get("action_type") == action_type:
            try:
                return int(float(action.get("value", 0)))
            except (ValueError, TypeError):
                return 0
    return 0


def _enrich_row(row: dict) -> dict:
    """Обогатить сырой insights-dict вычисленными полями."""
    try:
        spend = Decimal(str(row.get("spend", "0") or "0"))
    except Exception:
        spend = Decimal("0")

    leads = _extract_actions(row, "lead")
    cpl: Decimal | None = (spend / leads) if leads > 0 else None

    return {
        "ad_id": row.get("ad_id", ""),
        "ad_name": row.get("ad_name", "—"),
        "campaign_name": row.get("campaign_name", ""),
        "adset_name": row.get("adset_name", ""),
        "spend": spend,
        "impressions": int(row.get("impressions", 0) or 0),
        "clicks": int(row.get("clicks", 0) or 0),
        "leads": leads,
        "cpl": cpl,
        # effective_status может отсутствовать в insights (нет в default fields)
        "status": row.get("effective_status", "unknown"),
    }


def _filter_rows(raw_rows: list[dict], flt: dict[str, Any]) -> list[dict]:
    """Применить фильтры к списку обогащённых строк."""
    result = []
    for raw in raw_rows:
        r = _enrich_row(raw)

        # Фильтр по spend
        if "spend_gt" in flt and r["spend"] <= Decimal(str(flt["spend_gt"])):
            continue
        if "spend_lt" in flt and r["spend"] >= Decimal(str(flt["spend_lt"])):
            continue

        # Фильтр по CPL (пропускаем если CPL None и фильтр задан)
        if "cpl_gt" in flt:
            if r["cpl"] is None or r["cpl"] <= Decimal(str(flt["cpl_gt"])):
                continue
        if "cpl_lt" in flt:
            if r["cpl"] is None or r["cpl"] >= Decimal(str(flt["cpl_lt"])):
                continue

        # Фильтр по offer_code: вхождение в имя кампании или объявления
        if "offer_code" in flt:
            code = str(flt["offer_code"]).upper()
            haystack = (r["campaign_name"] + " " + r["ad_name"]).upper()
            if code not in haystack:
                continue

        # Фильтр по статусу
        if "status" in flt and flt["status"] != "any":
            if r["status"].upper() != str(flt["status"]).upper():
                continue

        result.append(r)
    return result


def _format_find_ads_result(
    rows: list[dict],
    account_id: str,
    date_preset: str,
    flt: dict,
) -> str:
    """Форматировать результат поиска в читаемый текст."""
    if not rows:
        return f"find_ads ({account_id}, {date_preset}): ни одно объявление не соответствует фильтру {flt}"

    lines = [
        f"find_ads: {account_id} | {date_preset} | найдено {len(rows)} объявлений:",
        "",
    ]
    for i, r in enumerate(rows, 1):
        cpl_str = f"CPL={r['cpl']:.2f}" if r["cpl"] is not None else "CPL=n/a"
        lines.append(f"  {i}. [{r['ad_id']}] {r['ad_name'][:55]}")
        lines.append(
            f"     spend={r['spend']:.2f}  leads={r['leads']}  {cpl_str}  status={r['status']}"
        )
    return "\n".join(lines)
