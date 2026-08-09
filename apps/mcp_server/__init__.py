# -*- coding: utf-8 -*-
"""MCP-сервер для FB Agent.

Экспонирует наши AI tools и набор JSON-снимков как ресурсы через
Model Context Protocol (https://modelcontextprotocol.io). Транспорт — stdio,
Claude Desktop сам поднимает процесс по конфигу claude_desktop_config.json.

Tools регистрируются через адаптер `tool_adapter.adapt_to_mcp_tool` поверх
`core.ai_assistant.tools.GLOBAL_REGISTRY`.
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> None:  # pragma: no cover - тонкая обёртка
    """Точка входа для CLI (run_mcp_server.py делает то же)."""
    import asyncio

    from apps.mcp_server.main import main as _async_main

    asyncio.run(_async_main())
