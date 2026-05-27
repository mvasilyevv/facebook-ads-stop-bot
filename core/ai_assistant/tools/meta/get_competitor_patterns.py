# -*- coding: utf-8 -*-
"""Tool get_competitor_patterns — топ паттернов конкурентов из ad_library_*.

На Этапе 3 — заглушка. Реальная реализация подключается на Этапе 4 (Ad Library):
агрегатор из ad_library_tier/report подаст top-K паттернов по слоту+стране.
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolContext


class GetCompetitorPatternsTool:
    """ЗАГЛУШКА: вернёт топ паттернов рекламы конкурентов после Этапа 4.

    Сейчас возвращает осмысленную «сейчас недоступно» строку — LLM сможет
    предупредить пользователя, что фича в работе.
    """

    name: ClassVar[str] = "get_competitor_patterns"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_competitor_patterns",
        "description": (
            "Топ паттернов рекламы конкурентов (hook/CTA/proof) для слот+страны. "
            "ВНИМАНИЕ: фича в разработке (Этап 4 META_INTEGRATION_PLAN). "
            "Используй только если пользователь явно просит срез по конкурентам."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slot": {"type": "string", "description": "ad library слот, e.g. 'chicken road 2'"},
                "country": {
                    "type": "string",
                    "description": "ISO-2 (KE, ZA, ...)",
                    "minLength": 2,
                    "maxLength": 2,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "required": ["slot", "country"],
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        slot = str(args.get("slot") or "").strip()
        country = str(args.get("country") or "").strip().upper()
        return (
            f"Get_competitor_patterns пока не реализован (slot={slot!r}, country={country!r}). "
            "Будет подключён на Этапе 4 META_INTEGRATION_PLAN — обзор Ad Library "
            "с агрегацией ad_library_tier/report. Сейчас вместо tool: используй /spy в TG, "
            "это запускает Ad Library pipeline прямо сейчас."
        )
