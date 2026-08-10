# -*- coding: utf-8 -*-
"""Адаптер ToolHandler → mcp.types.Tool.

Наши tool-классы используют JSON Schema формата Anthropic tool-use:
`{"name": ..., "description": ..., "input_schema": {...}}` (snake_case).
MCP протокол требует camelCase `inputSchema`. Поле `input_schema` может
отсутствовать (теоретически) — тогда подставляем
   пустой object schema, иначе MCP-клиент откажется регистрировать tool.
"""

from __future__ import annotations

from typing import Any

from mcp import types

from core.ai_assistant.tools.base import ToolHandler

_EMPTY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _extract_input_schema(handler: ToolHandler) -> dict[str, Any]:
    """Берём input_schema из ToolHandler.schema, fallback на пустой object."""
    raw = handler.schema.get("input_schema")
    if isinstance(raw, dict) and raw:
        return raw
    return dict(_EMPTY_INPUT_SCHEMA)


def adapt_to_mcp_tool(handler: ToolHandler) -> types.Tool:
    """Перевести наш ToolHandler в mcp.types.Tool.

    Имя совпадает с handler.name. inputSchema копируется из handler.schema.
    Description копируется из схемы без mutation-specific promises.
    """
    return types.Tool(
        name=handler.name,
        description=str(handler.schema.get("description") or ""),
        inputSchema=_extract_input_schema(handler),
    )


__all__ = ["adapt_to_mcp_tool"]
