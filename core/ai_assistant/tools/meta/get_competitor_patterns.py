# -*- coding: utf-8 -*-
"""Tool get_competitor_patterns — заглушка до Этапа 4 (Meta Ad Library)."""

from __future__ import annotations

from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel


class GetCompetitorPatternsTool:
    """Паттерны хуков/CTA конкурентов из Meta Ad Library.

    ВРЕМЕННО: заглушка. Реальная реализация — Этап 4 (Ad Library API).
    """

    name: ClassVar[str] = "get_competitor_patterns"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_competitor_patterns",
        "description": (
            "Паттерны хуков/CTA конкурентов из Meta Ad Library. "
            "ВРЕМЕННО недоступно (Этап 4 не завершён)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vertical": {
                    "type": "string",
                    "description": "Вертикаль, например 'finance', 'health', 'dating'",
                },
                "country": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 2,
                    "description": "Двухбуквенный код страны (ISO 3166-1), например 'UA'",
                },
            },
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Вернуть сообщение о заглушке."""
        return (
            "Этап 4 не завершён: библиотека паттернов конкурентов пока пуста. "
            "Реализация поверх Meta Ad Library API запланирована."
        )
