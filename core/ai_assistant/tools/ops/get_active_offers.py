# -*- coding: utf-8 -*-
"""Tool get_active_offers — список активных офферов из catalog.offers."""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import text

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError


class GetActiveOffersTool:
    """Возвращает список активных офферов (code, name, vertical)."""

    name: ClassVar[str] = "get_active_offers"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_active_offers",
        "description": (
            "Список активных офферов (code, name, vertical). Используется для "
            "матчинга с названиями кампаний и для drafts/request_bulk_pause."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vertical": {
                    "type": "string",
                    "description": "Фильтр по вертикали (gambling/finance/...) — опционально",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
            },
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        engine = ctx.require_engine()
        vertical = (args.get("vertical") or "").strip().lower() or None
        try:
            limit = int(args.get("limit") or 100)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"limit должен быть целым: {exc}") from exc
        limit = max(1, min(limit, 200))

        params: dict[str, Any] = {"lim": limit}
        sql = "SELECT code, name, vertical FROM offers WHERE is_active = true "
        if vertical:
            sql += "AND LOWER(vertical) = :vert "
            params["vert"] = vertical
        sql += "ORDER BY code LIMIT :lim"

        async with engine.connect() as conn:
            rows = (await conn.execute(text(sql), params)).all()

        if not rows:
            return "Активных офферов нет."

        lines = [f"Активные офферы ({len(rows)}):"]
        for row in rows:
            code, name_, vert = row
            lines.append(f"- {code} — {name_} [{vert or 'без вертикали'}]")
        return "\n".join(lines)
