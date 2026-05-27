# -*- coding: utf-8 -*-
"""Unit-тесты адаптера ToolHandler → mcp.types.Tool.

Покрытие:
- READ_ONLY tool: description без префикса, schema копируется.
- DRAFT_REQUIRED tool: префикс "[ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ В TELEGRAM] ".
- input_schema → inputSchema (camelCase) — main mapping.
- Отсутствие input_schema → пустая object-schema (fallback).
- GLOBAL_REGISTRY реально адаптируется без падений.
- Идемпотентность: повторный adapt не накладывает второй префикс.
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


# DRAFT_REQUIRED: префикс приклеивается ровно один раз.
def test_adapt_draft_required_prepends_prefix() -> None:
    stub = _StubTool(
        "request_budget_change",
        RiskLevel.DRAFT_REQUIRED,
        description="Создать DRAFT задачу на изменение бюджета",
    )
    tool = adapt_to_mcp_tool(stub)
    assert tool.description is not None
    assert tool.description.startswith("[ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ В TELEGRAM] ")
    assert "Создать DRAFT задачу" in tool.description


# CREATIVE tools: префикс не нужен (они не правят прод).
def test_adapt_creative_no_prefix() -> None:
    stub = _StubTool(
        "generate_ad_copy",
        RiskLevel.CREATIVE,
        description="Сгенерировать варианты текста",
    )
    tool = adapt_to_mcp_tool(stub)
    assert tool.description == "Сгенерировать варианты текста"


# Адаптация дважды не накладывает второй префикс — на случай повторного list_tools.
def test_adapt_idempotent_for_draft() -> None:
    stub = _StubTool(
        "request_bulk_pause",
        RiskLevel.DRAFT_REQUIRED,
        description="Bulk pause draft",
    )
    once = adapt_to_mcp_tool(stub)
    # Симулируем "повторный заход" — модифицируем stub.description и адаптируем.
    stub.schema["description"] = once.description
    twice = adapt_to_mcp_tool(stub)
    assert twice.description == once.description


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
        # У DRAFT_REQUIRED — префикс обязан быть.
        if handler.risk_level == RiskLevel.DRAFT_REQUIRED:
            assert tool.description and tool.description.startswith(
                "[ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ В TELEGRAM] "
            )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
