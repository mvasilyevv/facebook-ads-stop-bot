# -*- coding: utf-8 -*-
"""Tool get_insights — Marketing API /act_X/insights через InsightsFetcher."""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError
from core.ai_assistant.tools.meta._currency import (
    fetch_account_currency,
    format_major_money,
)
from core.meta_api.errors import MetaApiError
from core.meta_api.insights.fetcher import InsightsFetcher, sum_spend
from core.meta_api.schemas import MetaInsightsRequest


def _parse_date_or_none(value: Any, field: str) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        raise ToolError(f"{field}: ожидаю ISO-дату YYYY-MM-DD, получил {value!r}") from exc


class GetInsightsTool:
    """Insights по кабинету через Marketing API.

    Если переданы ad_ids/campaign_ids — фильтрует. Иначе — топ N по spend
    через level=ad/campaign/account.
    """

    name: ClassVar[str] = "get_insights"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_insights",
        "description": (
            "Marketing API GET /act_X/insights через активную Vision-сессию. "
            "Поля spend/impressions/clicks/cpc/ctr/cpm/actions. "
            "Параметры: ad_account_id (act_X), date_preset (today/yesterday/last_7d/last_30d) "
            "ИЛИ since+until, level (ad/adset/campaign/account), filtering по ad_ids/campaign_ids, "
            "limit (до 100)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string", "description": "act_X identifier"},
                "date_preset": {
                    "type": "string",
                    "enum": ["today", "yesterday", "last_3d", "last_7d", "last_14d", "last_30d"],
                },
                "since": {"type": "string", "description": "YYYY-MM-DD (вместе с until)"},
                "until": {"type": "string", "description": "YYYY-MM-DD (вместе с since)"},
                "level": {
                    "type": "string",
                    "enum": ["ad", "adset", "campaign", "account"],
                    "default": "ad",
                },
                "ad_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                "campaign_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
            "required": ["ad_account_id"],
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        client = ctx.require_meta_api()
        fetcher = InsightsFetcher(client)

        ad_account_id = str(args.get("ad_account_id") or "").strip()
        if not ad_account_id.startswith("act_"):
            raise ToolError("ad_account_id должен начинаться с 'act_'")

        date_preset = args.get("date_preset")
        since = _parse_date_or_none(args.get("since"), "since")
        until = _parse_date_or_none(args.get("until"), "until")
        if (since and not until) or (until and not since):
            raise ToolError("since и until задаются только парой")
        if not date_preset and not (since and until):
            date_preset = "today"

        level = str(args.get("level") or "ad").lower()
        if level not in ("ad", "adset", "campaign", "account"):
            raise ToolError(f"level должен быть ad/adset/campaign/account, получено {level!r}")

        try:
            limit = int(args.get("limit") or 25)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"limit должен быть целым: {exc}") from exc
        limit = max(1, min(limit, 100))

        ad_ids = [str(x) for x in (args.get("ad_ids") or []) if x]
        campaign_ids = [str(x) for x in (args.get("campaign_ids") or []) if x]
        if ad_ids and campaign_ids:
            raise ToolError("Указывай либо ad_ids, либо campaign_ids, не оба одновременно")

        try:
            if ad_ids:
                rows = await fetcher.fetch_for_ads(
                    ad_account_id=ad_account_id, ad_ids=ad_ids, date_preset=date_preset
                )
            elif campaign_ids:
                rows = await fetcher.fetch_for_campaigns(
                    ad_account_id=ad_account_id,
                    campaign_ids=campaign_ids,
                    date_preset=date_preset,
                    level=level if level != "ad" else "campaign",
                )
            else:
                req = MetaInsightsRequest(
                    ad_account_id=ad_account_id,
                    level=level,
                    date_preset=date_preset,
                    since=since,
                    until=until,
                    limit=limit,
                )
                rows = await fetcher.fetch_for_request(req)
        except MetaApiError as exc:
            raise ToolError(f"Marketing API: {exc}") from exc

        if not rows:
            return "Insights пуст — фильтры не дали результата."

        currency = await fetch_account_currency(client, ad_account_id)
        lines = [
            f"Insights (level={level}, rows={len(rows)}, "
            f"currency={currency.code if currency else 'unknown'}, "
            f"total_spend={format_major_money(sum_spend(rows), currency)}):"
        ]
        for row in rows[:limit]:
            ctr_str = f"{row.ctr:.2%}" if row.ctr is not None else "—"
            cpc_str = format_major_money(row.cpc, currency)
            entity_id = row.ad_id or row.campaign_id or "?"
            lines.append(
                f"- id={entity_id} spend={format_major_money(row.spend, currency)} "
                f"impr={row.impressions} "
                f"clicks={row.clicks} cpc={cpc_str} ctr={ctr_str}"
            )
        return "\n".join(lines)
