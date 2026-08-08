# -*- coding: utf-8 -*-
"""Tool find_ads — поиск объявлений в кабинете через GET /act_X/ads."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError
from core.meta_api.errors import MetaApiError


class FindAdsTool:
    """Поиск ad по name LIKE / campaign_id / effective_status.

    Использует GET /act_X/ads с filtering и fields=name,effective_status,campaign,adset.
    """

    name: ClassVar[str] = "find_ads"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "find_ads",
        "description": (
            "Поиск объявлений в Marketing API GET /act_X/ads. "
            "Фильтрация по name (substring), campaign_id, effective_status. "
            "Возвращает ad_id, name, campaign_name, effective_status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string", "description": "act_X identifier"},
                "name_contains": {"type": "string"},
                "campaign_id": {"type": "string"},
                "effective_status": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "ACTIVE",
                            "PAUSED",
                            "DELETED",
                            "ARCHIVED",
                            "DISAPPROVED",
                            "PENDING_REVIEW",
                            "WITH_ISSUES",
                            "CAMPAIGN_PAUSED",
                            "ADSET_PAUSED",
                        ],
                    },
                    "maxItems": 5,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
            "required": ["ad_account_id"],
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        client = ctx.require_meta_api()
        ad_account_id = str(args.get("ad_account_id") or "").strip()
        if not ad_account_id.startswith("act_"):
            raise ToolError("ad_account_id должен начинаться с 'act_'")

        name_contains = (args.get("name_contains") or "").strip()
        campaign_id = (args.get("campaign_id") or "").strip()
        statuses = [str(s).upper() for s in (args.get("effective_status") or []) if s]
        try:
            limit = int(args.get("limit") or 25)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"limit должен быть целым: {exc}") from exc
        limit = max(1, min(limit, 100))

        filtering: list[dict[str, Any]] = []
        if name_contains:
            filtering.append({"field": "ad.name", "operator": "CONTAIN", "value": name_contains})
        if campaign_id:
            filtering.append({"field": "campaign.id", "operator": "EQUAL", "value": campaign_id})
        if statuses:
            filtering.append({"field": "ad.effective_status", "operator": "IN", "value": statuses})

        params: dict[str, str] = {
            "fields": "id,name,effective_status,status,campaign{id,name},adset{id,name}",
            "limit": str(limit),
        }
        if filtering:
            params["filtering"] = json.dumps(filtering)

        try:
            response = await client.execute_graph_call(
                method="GET",
                endpoint=f"/{ad_account_id}/ads",
                query_params=params,
                ad_account_id=ad_account_id,
            )
        except MetaApiError as exc:
            raise ToolError(f"Marketing API: {exc}") from exc

        data = response.get("data") or []
        if not data:
            return "Подходящих объявлений нет."

        lines = [f"Найдено {len(data)} ads:"]
        for item in data[:limit]:
            ad_id = item.get("id")
            name_ = (item.get("name") or "")[:60]
            eff = item.get("effective_status") or "?"
            campaign = (item.get("campaign") or {}).get("name") or "?"
            adset = (item.get("adset") or {}).get("name") or "?"
            lines.append(f"- {ad_id} [{eff}] «{name_}» / camp={campaign[:30]} / adset={adset[:30]}")
        return "\n".join(lines)
