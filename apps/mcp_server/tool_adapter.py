# -*- coding: utf-8 -*-
"""Адаптер ToolHandler → mcp.types.Tool.

Наши tool-классы используют JSON Schema формата Anthropic tool-use:
`{"name": ..., "description": ..., "input_schema": {...}}` (snake_case).
MCP протокол требует camelCase `inputSchema`. Маппинг прямой, но с двумя
нюансами:

1. DRAFT_REQUIRED tools получают префикс в description — чтобы LLM в Claude
   Desktop сразу понимал, что после вызова потребуется подтверждение в TG.
2. Поле `input_schema` может отсутствовать (теоретически) — тогда подставляем
   пустой object schema, иначе MCP-клиент откажется регистрировать tool.
"""

from __future__ import annotations

from typing import Any

from mcp import types

from core.ai_assistant.tools.base import RiskLevel, ToolHandler

_DRAFT_PREFIX = "[ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ В TELEGRAM] "

_EMPTY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _decorate_description(handler: ToolHandler) -> str:
    raw = str(handler.schema.get("description") or "")
    if handler.risk_level == RiskLevel.DRAFT_REQUIRED and _DRAFT_PREFIX not in raw:
        return _DRAFT_PREFIX + raw
    return raw


def _extract_input_schema(handler: ToolHandler) -> dict[str, Any]:
    """Берём input_schema из ToolHandler.schema, fallback на пустой object."""
    raw = handler.schema.get("input_schema")
    if isinstance(raw, dict) and raw:
        return raw
    return dict(_EMPTY_INPUT_SCHEMA)


def adapt_to_mcp_tool(handler: ToolHandler) -> types.Tool:
    """Перевести наш ToolHandler в mcp.types.Tool.

    Имя совпадает с handler.name. inputSchema копируется из handler.schema.
    Description для DRAFT_REQUIRED префиксуется.
    """
    return types.Tool(
        name=handler.name,
        description=_decorate_description(handler),
        inputSchema=_extract_input_schema(handler),
    )


__all__ = ["adapt_to_mcp_tool"]
