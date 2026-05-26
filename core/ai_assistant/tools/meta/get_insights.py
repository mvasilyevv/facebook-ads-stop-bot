# -*- coding: utf-8 -*-
"""Tool get_insights — метрики рекламного кабинета через Marketing API."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

from clients.python_grpc.meta_api_client import MetaApiError
from core.ai_assistant.tools.base import RiskLevel, ToolError
from core.meta_api.client import MetaApiHighLevelClient


class GetInsightsTool:
    """Получить метрики (spend, impressions, clicks, leads, deposits) с Marketing API.

    READ_ONLY — исполняется без подтверждения.
    """

    name: ClassVar[str] = "get_insights"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_insights",
        "description": (
            "Получить метрики (spend, impressions, clicks, leads, deposits) с Marketing API "
            "для рекламного кабинета. Параметры: level (ad/adset/campaign/account), "
            "date_preset (today/yesterday/last_7d/last_30d/last_90d), limit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {
                    "type": "string",
                    "description": "act_XXXXXXXXX или просто число",
                },
                "level": {
                    "type": "string",
                    "enum": ["ad", "adset", "campaign", "account"],
                    "default": "ad",
                },
                "date_preset": {
                    "type": "string",
                    "enum": [
                        "today",
                        "yesterday",
                        "last_3d",
                        "last_7d",
                        "last_14d",
                        "last_30d",
                        "last_90d",
                    ],
                    "default": "today",
                },
                "limit": {"type": "integer", "default": 20, "maximum": 500},
            },
            "required": ["ad_account_id"],
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Запросить insights через MetaApiHighLevelClient и вернуть текстовое summary."""
        # Нормализуем ad_account_id — добавляем "act_" если нет
        raw_account_id = str(args.get("ad_account_id", "")).strip()
        if not raw_account_id:
            raise ToolError("ad_account_id не указан")
        if not raw_account_id.startswith("act_"):
            raw_account_id = f"act_{raw_account_id}"

        level = str(args.get("level", "ad"))
        date_preset = str(args.get("date_preset", "today"))
        limit = min(int(args.get("limit", 20)), 500)

        try:
            async with MetaApiHighLevelClient() as client:
                rows = await client.get_insights(
                    raw_account_id,
                    level=level,
                    date_preset=date_preset,
                    limit=limit,
                )
        except MetaApiError as exc:
            raise ToolError(f"Marketing API вернул ошибку (code={exc.code}): {exc}") from exc
        except Exception as exc:
            raise ToolError(f"Не удалось получить insights: {exc}") from exc

        if not rows:
            return (
                f"Insights для {raw_account_id} за {date_preset} (level={level}): "
                "данные отсутствуют или кабинет не активен."
            )

        return _format_insights_summary(rows, raw_account_id, date_preset, level)


# ── Вспомогательные функции ──────────────────────────────────────────────────


def _extract_actions(row: dict, action_type: str) -> int:
    """Извлечь значение action_type из поля actions (список dict)."""
    for action in row.get("actions", []):
        if action.get("action_type") == action_type:
            try:
                return int(float(action.get("value", 0)))
            except (ValueError, TypeError):
                return 0
    return 0


def _format_insights_summary(
    rows: list[dict],
    account_id: str,
    date_preset: str,
    level: str,
) -> str:
    """Сформировать читаемое текстовое summary из списка insights-строк."""
    # Агрегируем общие цифры
    total_spend = Decimal("0")
    total_impressions = 0
    total_clicks = 0
    total_leads = 0

    # Для топ-5 по spend — собираем записи
    enriched: list[dict] = []
    for row in rows:
        try:
            spend = Decimal(str(row.get("spend", "0") or "0"))
        except Exception:
            spend = Decimal("0")
        impressions = int(row.get("impressions", 0) or 0)
        clicks = int(row.get("clicks", 0) or 0)
        leads = _extract_actions(row, "lead")

        total_spend += spend
        total_impressions += impressions
        total_clicks += clicks
        total_leads += leads

        enriched.append(
            {
                "name": row.get(f"{level}_name", row.get("ad_name", "—")),
                "spend": spend,
                "impressions": impressions,
                "clicks": clicks,
                "leads": leads,
            }
        )

    # Сортируем по spend убыванием
    enriched.sort(key=lambda r: r["spend"], reverse=True)
    top5 = enriched[:5]

    lines = [
        f"Insights: {account_id} | {date_preset} | level={level} | строк={len(rows)}",
        f"Итого: spend={total_spend:.2f}, impressions={total_impressions:,}, "
        f"clicks={total_clicks:,}, leads={total_leads}",
        "",
        f"Топ-{len(top5)} по spend:",
    ]
    for i, r in enumerate(top5, 1):
        cpl = (r["spend"] / r["leads"]) if r["leads"] > 0 else None
        cpl_str = f"  CPL={cpl:.2f}" if cpl is not None else ""
        lines.append(
            f"  {i}. {r['name'][:60]} — spend={r['spend']:.2f}, leads={r['leads']}{cpl_str}"
        )

    return "\n".join(lines)
