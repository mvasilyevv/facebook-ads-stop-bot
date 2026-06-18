# -*- coding: utf-8 -*-
"""Integration: MCP server call_tool → реальный pg_engine + fake_redis.

Покрывает три ключевых сценария call_tool:
1. READ_ONLY tool (`get_active_offers`) — реальный SQL к offers, ответ — TextContent.
2. DRAFT_REQUIRED tool (`request_budget_change`) — отключён (MCP read-only),
   НЕ создаёт строку в task_queue + не экспонируется в list_tools.
3. Неизвестный tool — TextContent с сообщением "Неизвестный tool".

Дополнительно: rate-limit per client_key через fake_redis (лимит 30/час).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from mcp import types
from mcp.server.lowlevel import Server
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.mcp_server.context import MCPContextManager
from apps.mcp_server.main import build_server


async def _invoke_call_tool(app: Server, name: str, arguments: dict) -> list[types.TextContent]:
    """Достать зарегистрированный CallTool handler и дёрнуть его напрямую.

    Server.request_handlers — это словарь mcp.types.<Request> → async callable.
    Мы строим CallToolRequest + ServerResult и возвращаем content[].
    """
    handler = app.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    server_result = await handler(request)
    # ServerResult обёртка → внутри CallToolResult с content list.
    call_result: types.CallToolResult = server_result.root  # type: ignore[assignment]
    return list(call_result.content)


@pytest_asyncio.fixture
async def seeded_offer(pg_engine: AsyncEngine):
    """Закидывает один активный offer с уникальным code — потом удаляет."""
    offer_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8].upper()
    code = f"MCP_{suffix}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, :n, true)"),
            {"i": offer_id, "c": code, "n": f"Test MCP {suffix}"},
        )
    yield code
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})


@pytest_asyncio.fixture
async def clean_meta_tasks(pg_engine: AsyncEngine):
    """Чистит task_queue от meta_api_mutation до/после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'meta_api_mutation'"))

    await _truncate()
    yield
    await _truncate()


# READ_ONLY: get_active_offers возвращает реальные офферы из БД одним TextContent.
@pytest.mark.asyncio
async def test_call_tool_read_only_returns_offers(
    pg_engine: AsyncEngine,
    fake_redis_client,
    seeded_offer: str,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    contents = await _invoke_call_tool(app, "get_active_offers", {"limit": 200})
    assert len(contents) == 1
    block = contents[0]
    assert block.type == "text"
    assert seeded_offer in block.text


# DRAFT_REQUIRED отключён в MCP (read-only): request_budget_change → отказ, БЕЗ записи.
@pytest.mark.asyncio
async def test_call_tool_draft_is_disabled(
    pg_engine: AsyncEngine,
    fake_redis_client,
    clean_meta_tasks,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    contents = await _invoke_call_tool(
        app,
        "request_budget_change",
        {
            "adset_id": "23000999",
            "ad_account_id": "act_42",
            "daily_budget_usd": 12.34,
            "reason": "mcp integration test",
        },
    )
    assert len(contents) == 1
    assert "отключён" in contents[0].text

    # Никакой строки в task_queue: write-мутации через MCP недоступны.
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT 1 FROM task_queue
                    WHERE task_type = 'meta_api_mutation'
                    ORDER BY id DESC LIMIT 1
                    """
                )
            )
        ).first()
    assert row is None


# DRAFT_REQUIRED tools не экспонируются в list_tools — MCP остаётся read-only.
@pytest.mark.asyncio
async def test_list_tools_excludes_draft(
    pg_engine: AsyncEngine,
    fake_redis_client,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    handler = app.request_handlers[types.ListToolsRequest]
    request = types.ListToolsRequest(method="tools/list")
    server_result = await handler(request)
    tools: list[types.Tool] = server_result.root.tools  # type: ignore[assignment]

    names = {t.name for t in tools}
    assert names, "list_tools пуст — должны остаться read-only инструменты"
    # Ни одного request_* (DRAFT_REQUIRED) в списке.
    assert not any(n.startswith("request_") for n in names), names


# Неизвестный tool возвращает TextContent с пояснением, не падает.
@pytest.mark.asyncio
async def test_call_tool_unknown_returns_error_text(
    pg_engine: AsyncEngine,
    fake_redis_client,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    contents = await _invoke_call_tool(app, "definitely_no_such_tool", {})
    assert len(contents) == 1
    assert "Неизвестный tool" in contents[0].text


# Rate-limit: тридцать первый вызов после лимита возвращает текст про ⏱.
@pytest.mark.asyncio
async def test_call_tool_rate_limit_exhausts(
    pg_engine: AsyncEngine,
    fake_redis_client,
    seeded_offer: str,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    # Лимит по умолчанию 30/час. Делаем 30 успешных + один отказ.
    for _ in range(30):
        contents = await _invoke_call_tool(app, "get_active_offers", {})
        assert contents[0].type == "text"

    over = await _invoke_call_tool(app, "get_active_offers", {})
    # Лимит превышен → ответ начинается с ⏱ (см. main.py).
    assert "⏱" in over[0].text or "Превышен лимит" in over[0].text
