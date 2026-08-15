# -*- coding: utf-8 -*-
"""Tool get_active_offers — список активных офферов из catalog.offers."""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import text

from core.ad_account_catalog import ad_account_catalog
from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError


class GetActiveOffersTool:
    """Возвращает список активных офферов (code, name, vertical)."""

    name: ClassVar[str] = "get_active_offers"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_active_offers",
        "description": (
            "Список активных офферов (code, name, vertical). Используется для "
            "матчинга с названиями кампаний."
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
            raise ToolError("limit должен быть целым") from exc
        limit = max(1, min(limit, 200))

        params: dict[str, Any] = {"lim": limit}
        sql = "SELECT id, code, name, vertical FROM offers WHERE is_active = true "
        if vertical:
            sql += "AND LOWER(vertical) = :vert "
            params["vert"] = vertical
        sql += "ORDER BY code LIMIT :lim"

        async with engine.connect() as conn:
            rows = (await conn.execute(text(sql), params)).mappings().all()
            account_ids_by_offer = await ad_account_catalog.list_by_offer(
                conn,
                offer_ids=(row["id"] for row in rows),
            )

        if not rows:
            return "Активных офферов нет."

        lines = [f"Активные офферы ({len(rows)}):"]
        for row in rows:
            code = row["code"]
            name_ = row["name"]
            vert = row["vertical"]
            accounts = account_ids_by_offer.get(row["id"], [])
            # Мульти-кабинет: оффер без кабинетов НЕ сканируется — явный маркер для LLM.
            acc_part = (
                f"кабинеты: {', '.join(accounts)}"
                if accounts
                else "⚠️ кабинеты не заданы — НЕ сканируется"
            )
            lines.append(f"- {code} — {name_} [{vert or 'без вертикали'}] · {acc_part}")
        return "\n".join(lines)
