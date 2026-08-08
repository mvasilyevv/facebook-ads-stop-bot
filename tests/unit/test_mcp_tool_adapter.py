# -*- coding: utf-8 -*-
"""Unit-тесты адаптера ToolHandler → mcp.types.Tool.

Покрытие:
- READ_ONLY tool: description и schema копируются.
- input_schema → inputSchema (camelCase) — main mapping.
- Отсутствие input_schema → пустая object-schema (fallback).
- GLOBAL_REGISTRY реально адаптируется без падений.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.mcp_server.tool_adapter import adapt_to_mcp_tool
from core.ai_assistant.tools import GLOBAL_REGISTRY
from core.ai_assistant.tools.base import RiskLevel


class _StubTool:
    """Минимальная реализация ToolHandler-протокола для адаптера."""

    def __init__(
        self,
        name: str,
        risk: RiskLevel,
        *,
        description: str = "stub description",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.risk_level = risk
        schema: dict[str, Any] = {"name": name, "description": description}
        if input_schema is not None:
            schema["input_schema"] = input_schema
        self.schema = schema

    async def run(self, ctx: Any, args: dict[str, Any]) -> str:  # pragma: no cover - не вызывается
        return ""


# READ_ONLY: описание копируется без префикса.
def test_adapt_read_only_keeps_description_as_is() -> None:
    stub = _StubTool(
        "list_offers",
        RiskLevel.READ_ONLY,
        description="Список офферов",
        input_schema={"type": "object", "properties": {}},
    )
    tool = adapt_to_mcp_tool(stub)
    assert tool.name == "list_offers"
    assert tool.description == "Список офферов"
    assert tool.inputSchema == {"type": "object", "properties": {}}


# CREATIVE tools: префикс не нужен (они не правят прод).
def test_adapt_creative_no_prefix() -> None:
    stub = _StubTool(
        "generate_ad_copy",
        RiskLevel.CREATIVE,
        description="Сгенерировать варианты текста",
    )
    tool = adapt_to_mcp_tool(stub)
    assert tool.description == "Сгенерировать варианты текста"


# Без input_schema адаптер подставляет пустую object-схему — иначе MCP откажет.
def test_adapt_missing_input_schema_falls_back_to_empty_object() -> None:
    stub = _StubTool("no_args", RiskLevel.READ_ONLY, input_schema=None)
    tool = adapt_to_mcp_tool(stub)
    assert tool.inputSchema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


# Реальные tools из GLOBAL_REGISTRY все адаптируются без исключений.
def test_global_registry_all_tools_adapt_without_errors() -> None:
    names = GLOBAL_REGISTRY.list_names()
    assert names, "GLOBAL_REGISTRY пустой — должно быть >=1 tools"
    for name in names:
        handler = GLOBAL_REGISTRY.get(name)
        assert handler is not None
        tool = adapt_to_mcp_tool(handler)
        assert tool.name == name
        # У MCP Tool inputSchema всегда непустой dict.
        assert isinstance(tool.inputSchema, dict)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
