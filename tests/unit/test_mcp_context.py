# -*- coding: utf-8 -*-
"""Unit-тесты MCPContextManager.

Покрытие:
- build_tool_context: возвращает ToolContext с client_key="mcp:claude-desktop".
- Поля engine/redis_client/meta_api_client пробрасываются как есть.
- _safe_dsn маскирует пароль.
- Без __aenter__ context — все поля None, не падает.

Lifecycle с реальным engine/redis вынесен в integration-тесты — иначе
unit зависит от live Postgres / Redis.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.mcp_server.context import MCP_CLIENT_KEY, MCPContextManager, _safe_dsn
from core.ai_assistant.tools.base import ToolContext


# build_tool_context возвращает ToolContext со стабильным client_key.
def test_build_tool_context_client_key() -> None:
    mgr = MCPContextManager()
    ctx = mgr.build_tool_context()
    assert isinstance(ctx, ToolContext)
    assert ctx.client_key == MCP_CLIENT_KEY
    assert ctx.client_key == "mcp:claude-desktop"
    # requested_by пустой → effective_requested_by вернёт "ai:mcp:claude-desktop".
    assert ctx.effective_requested_by() == "ai:mcp:claude-desktop"


# Поля engine/redis/meta_api пробрасываются в ToolContext один-в-один.
def test_build_tool_context_forwards_dependencies() -> None:
    engine = MagicMock(name="engine")
    redis_client = MagicMock(name="redis")
    meta = MagicMock(name="meta_api")
    mgr = MCPContextManager()
    mgr.engine = engine
    mgr.redis_client = redis_client
    mgr.meta_api_client = meta

    ctx = mgr.build_tool_context()
    assert ctx.engine is engine
    assert ctx.redis_client is redis_client
    assert ctx.meta_api_client is meta


# До __aenter__ все ресурсы None — require_* поднимет ToolError в run().
def test_default_fields_none_before_enter() -> None:
    mgr = MCPContextManager()
    assert mgr.engine is None
    assert mgr.redis_client is None
    assert mgr.meta_api_client is None


# _safe_dsn не должен оставлять в логах credentials и query secrets.
def test_safe_dsn_masks_password() -> None:
    masked = _safe_dsn("postgresql+asyncpg://user:secret@localhost:5432/db?sslkey=private-key")
    assert "secret" not in masked
    assert "private-key" not in masked
    assert "user" not in masked
    assert "localhost" in masked


# DSN без @ возвращается как есть.
def test_safe_dsn_no_at_returns_as_is() -> None:
    assert _safe_dsn("sqlite:///:memory:") == "sqlite:///:memory:"


# Повторный __aexit__ без __aenter__ — не падает.
@pytest.mark.asyncio
async def test_aexit_without_aenter_is_noop() -> None:
    mgr = MCPContextManager()
    await mgr.__aexit__(None, None, None)
    # Никаких атрибутов engine/redis не должно появиться.
    assert mgr.engine is None
    assert mgr.redis_client is None
