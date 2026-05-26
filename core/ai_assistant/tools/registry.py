# -*- coding: utf-8 -*-
"""ToolRegistry — реестр доступных tools.

Каждая регистрация = валидация уникальности имени + добавление в индекс.
"""

from __future__ import annotations

import logging
from typing import Any

from core.ai_assistant.tools.base import RiskLevel, ToolError, ToolHandler

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Реестр доступных tools.

    Schema tools автоматически доступна через метод schemas().
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolHandler] = {}

    def register(self, tool: ToolHandler) -> None:
        """Зарегистрировать новый tool. Поднимает ValueError если name уже занят."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' уже зарегистрирован")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolHandler | None:
        """Вернуть tool по имени или None."""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """Отсортированный список имён зарегистрированных tools."""
        return sorted(self._tools.keys())

    def list_by_risk(self, risk_level: RiskLevel) -> list[ToolHandler]:
        """Список tools с заданным уровнем риска."""
        return [t for t in self._tools.values() if t.risk_level == risk_level]

    def schemas(self) -> list[dict[str, Any]]:
        """Список JSON Schema всех tools — передаётся в Anthropic как tools=."""
        return [t.schema for t in self._tools.values()]

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        """Исполнить tool по имени. ToolError ловится и перебрасывается как есть."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"Неизвестный tool: '{name}'")
        try:
            return await tool.run(args)
        except ToolError:
            raise
        except Exception as exc:
            logger.exception("Tool '%s' упал с непредвиденной ошибкой", name)
            raise ToolError(f"Внутренняя ошибка tool '{name}': {exc}") from exc


# Глобальный реестр — заполняется при импорте ops submodule.
GLOBAL_REGISTRY = ToolRegistry()
