# -*- coding: utf-8 -*-
"""Точка входа MCP-сервера FB Stop Bot (stdio transport).

Регистрирует:
- list_tools / call_tool — поверх core.ai_assistant.tools.GLOBAL_REGISTRY
- list_resources / read_resource — 4 JSON-snapshot ресурса (см. resources.py)

Lifecycle: один MCPContextManager на процесс. Каждый call_tool / read_resource
переиспользует engine + redis + meta_api_client.

КРИТИЧНО: stdout — это бинарный канал MCP-протокола. Все логи идут в stderr
(см. run_mcp_server.py).
"""

from __future__ import annotations

import logging
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from apps.mcp_server.context import MCPContextManager
from apps.mcp_server.resources import (
    list_resources as build_resource_list,
)
from apps.mcp_server.resources import (
    read_resource as read_resource_impl,
)
from apps.mcp_server.tool_adapter import adapt_to_mcp_tool
from core.ai_assistant.tools import (
    GLOBAL_REGISTRY,
    RateLimitExceeded,
    ToolError,
    check_and_increment,
    execute_tool,
)
from core.ai_assistant.tools._ratelimit import _DEFAULT_MAX_PER_HOUR, _check_memory_fallback

logger = logging.getLogger(__name__)

# Лимит соответствует core.ai_assistant.tools._ratelimit и Settings.ai_rate_limit_per_hour.
# 30 запросов/час с одного MCP-клиента — этого достаточно для обычной диалоговой сессии.
_RATE_LIMIT_PER_HOUR = _DEFAULT_MAX_PER_HOUR


# Системный промпт MCP-сервера: клиент (Claude Desktop/Cursor) передаёт его модели
# при initialize — в отличие от resource, который модель может и не прочитать.
# Краткая версия schema-overview: контекст + money-правила. Полная инструкция —
# ресурс fb-stop-bot://schema-overview.
_SERVER_INSTRUCTIONS = (
    "FB Stop Bot — мониторинг и авто-стоп убыточной рекламы Facebook Ads. "
    "Здесь реальные деньги: не выдумывай данные, при сомнении читай ресурсы "
    "(fb-stop-bot://offers, recent-alerts, schema-overview — "
    "в последнем полная инструкция).\n\n"
    "Правила:\n"
    "1. MCP только read-only: write-мутации (пауза/бюджет/клон/создание кампании) "
    "отключены. Давай анализ и рекомендации, изменения рекламы пользователь делает сам.\n"
    "2. «Что отключить?» = read-only анализ (get_recent_alerts, get_offer_performance, "
    "get_tracker_stats) + рекомендация, без постановки mutation-задач.\n"
    "3. Мульти-кабинет: офферы привязаны к ad_account_ids; активный оффер без "
    "кабинетов не сканируется — подсвечивай это как проблему.\n"
    "4. Расхождение метрик Meta (spend) и трекера AdSet.pro (депозиты/ROI) — "
    "нормальный attribution gap, упоминай его в сравнениях."
)


def build_server(ctx_mgr: MCPContextManager) -> Server:
    """Собрать MCP Server и зарегистрировать хендлеры поверх ctx_mgr.

    Вынесено отдельной функцией — удобно дёргать из тестов без stdio.
    """
    app: Server = Server("fb-stop-bot", instructions=_SERVER_INSTRUCTIONS)

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        # Снимаем актуальное состояние GLOBAL_REGISTRY на каждом list_tools —
        # tools регистрируются на импорте, но тесты могут добавлять/убирать.
        handlers = [GLOBAL_REGISTRY.get(name) for name in GLOBAL_REGISTRY.list_names()]
        return [adapt_to_mcp_tool(h) for h in handlers if h is not None]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        handler = GLOBAL_REGISTRY.get(name)
        if handler is None:
            return [types.TextContent(type="text", text=f"Неизвестный tool: '{name}'")]
        tool_ctx = ctx_mgr.build_tool_context()

        # Rate-limit per client_key. НЕ fail-open (H-7, см. HIGH #13 раунда 6):
        # при недоступном Redis переключаемся на in-memory secondary cap
        # (_check_memory_fallback — тот же, что использует check_and_increment
        # при сбое Redis-вызова). Раньше redis_client=None (Redis не поднялся при
        # старте MCPContextManager, см. context.py) ИЛИ любая ошибка check_and_increment
        # пропускали лимит целиком — злоумышленник мог бомбардировать Meta API через
        # AI-tools при мёртвом Redis.
        try:
            if tool_ctx.redis_client is None:
                await _check_memory_fallback(tool_ctx.client_key)
            else:
                await check_and_increment(
                    tool_ctx.redis_client,
                    client_key=tool_ctx.client_key,
                    max_per_hour=_RATE_LIMIT_PER_HOUR,
                )
        except RateLimitExceeded as exc:
            logger.warning("MCP rate-limit: %s", exc)
            return [types.TextContent(type="text", text=f"⏱ {exc}")]

        try:
            result_text = await execute_tool(name, arguments, tool_ctx)
        except ToolError as exc:
            logger.info("Tool %s вернул ToolError: %s", name, exc)
            return [types.TextContent(type="text", text=f"Tool error: {exc}")]
        except Exception as exc:
            logger.exception("Tool %s упал необработанной ошибкой", name)
            return [types.TextContent(type="text", text=f"Внутренняя ошибка tool '{name}': {exc}")]

        return [types.TextContent(type="text", text=result_text)]

    @app.list_resources()
    async def list_resources() -> list[types.Resource]:
        return build_resource_list()

    @app.read_resource()
    async def read_resource(uri: Any) -> str:
        # mcp передаёт AnyUrl — приводим к str для удобства dispatcher'а.
        return await read_resource_impl(str(uri), ctx_mgr)

    return app


async def main() -> None:
    """Async entry: поднять MCPContextManager и запустить stdio loop.

    Loop работает до закрытия stdin (Claude Desktop останавливает процесс).
    """
    async with MCPContextManager() as ctx_mgr:
        app = build_server(ctx_mgr)
        logger.info(
            "MCP-сервер 'fb-stop-bot' запущен (tools=%d, transport=stdio)",
            len(GLOBAL_REGISTRY.list_names()),
        )
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())


__all__ = ["build_server", "main"]
