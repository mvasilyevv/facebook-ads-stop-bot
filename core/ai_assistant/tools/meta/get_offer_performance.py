# -*- coding: utf-8 -*-
"""Tool get_offer_performance — производительность оффера за период.

1) Берёт офферы из БД (по code/vertical).
2) Через Marketing API insights с filtering=campaign.name CONTAIN offer.code собирает суммарные метрики.
3) Возвращает aggregate spend/clicks/impressions/leads + cost_per_lead.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import text

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError
from core.meta_api.errors import MetaApiError
from core.meta_api.insights.fetcher import InsightsFetcher
from core.meta_api.schemas import MetaInsightsRequest


class GetOfferPerformanceTool:
    """Сводка по офферу: spend / clicks / impressions / leads / cost-per-lead."""

    name: ClassVar[str] = "get_offer_performance"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_offer_performance",
        "description": (
            "Сводная производительность оффера: суммарный spend/clicks/impressions/leads "
            "за date_preset. Match: campaign.name CONTAIN offer_code (case-insensitive)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string", "description": "act_X"},
                "offer_code": {"type": "string", "description": "Например DRC_CR2"},
                "date_preset": {
                    "type": "string",
                    "enum": ["today", "yesterday", "last_3d", "last_7d", "last_14d", "last_30d"],
                    "default": "today",
                },
            },
            "required": ["ad_account_id", "offer_code"],
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        engine = ctx.require_engine()
        client = ctx.require_meta_api()

        ad_account_id = str(args.get("ad_account_id") or "").strip()
        if not ad_account_id.startswith("act_"):
            raise ToolError("ad_account_id должен начинаться с 'act_'")
        offer_code = str(args.get("offer_code") or "").strip()
        if not offer_code:
            raise ToolError("offer_code обязателен")
        date_preset = str(args.get("date_preset") or "today")

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT code, name, vertical, is_active "
                        "FROM offers WHERE LOWER(code) = LOWER(:c) LIMIT 1"
                    ),
                    {"c": offer_code},
                )
            ).first()
        if not row:
            raise ToolError(
                f"Оффер {offer_code!r} не найден в catalog.offers — проверь /get_active_offers"
            )
        if not row[3]:
            return f"Оффер {offer_code} существует, но is_active=false — статистика не собирается."

        fetcher = InsightsFetcher(client)
        req = MetaInsightsRequest(
            ad_account_id=ad_account_id,
            level="ad",
            date_preset=date_preset,
            filtering=({"field": "campaign.name", "operator": "CONTAIN", "value": offer_code},),
            limit=200,
        )
        try:
            rows = await fetcher.fetch_for_request(req)
        except MetaApiError as exc:
            raise ToolError(f"Marketing API: {exc}") from exc

        if not rows:
            return f"Нет insights по офферу {offer_code} за {date_preset}."

        total_spend = sum((r.spend for r in rows), start=Decimal("0"))
        total_impr = sum(r.impressions for r in rows)
        total_clicks = sum(r.clicks for r in rows)
        leads = sum(r.actions.get("lead", 0) for r in rows)
        registrations = sum(r.actions.get("complete_registration", 0) for r in rows)
        deposits = sum(
            r.actions.get("offsite_conversion.custom.deposit", 0) or r.actions.get("purchase", 0)
            for r in rows
        )

        cpl = (total_spend / leads) if leads else None
        cpr = (total_spend / registrations) if registrations else None
        ctr_total = (Decimal(total_clicks) / Decimal(total_impr)) if total_impr else None

        cpl_str = f"${cpl:.2f}" if cpl is not None else "—"
        cpr_str = f"${cpr:.2f}" if cpr is not None else "—"
        ctr_str = f"{ctr_total:.2%}" if ctr_total is not None else "—"

        lines = [
            f"Оффер {offer_code} ({row[1]} / {row[2] or 'без вертикали'}) — {date_preset}",
            f"Объявлений: {len(rows)}",
            f"Spend: ${total_spend:.2f}",
            f"Impr: {total_impr}",
            f"Clicks: {total_clicks} (CTR={ctr_str})",
            f"Leads: {leads} (CPL={cpl_str})",
            f"Registrations: {registrations} (CPR={cpr_str})",
            f"Deposits: {deposits}",
            f"Match: campaign.name CONTAIN {offer_code!r} (filtering={json.dumps(list(req.filtering))[:100]})",
        ]
        return "\n".join(lines)
