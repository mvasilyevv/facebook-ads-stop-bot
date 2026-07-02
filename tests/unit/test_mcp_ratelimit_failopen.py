# -*- coding: utf-8 -*-
"""Unit: H-7 — MCP call_tool не fail-open при сбое/отсутствии Redis.

Контекст: apps/mcp_server/main.py раньше при ошибке check_and_increment (или
redis_client=None) полностью пропускал rate-limit ("fail-open" в комментарии).
Инвариант проекта (HIGH #13 раунда 6, core/ai_assistant/tools/_ratelimit.py) —
при недоступном Redis работает in-memory secondary cap (5 запросов/60с),
а не безлимитный пропуск. Фикс переносит этот инвариант и в MCP-ветку.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.mcp_server.context import MCPContextManager
from apps.mcp_server.main import build_server
from core.ai_assistant.tools._ratelimit import _reset_memory_fallback_for_tests
from core.ai_assistant.tools.base import RiskLevel, ToolContext
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

_TOOL_NAME = "test_mcp_ratelimit_probe"


class _EchoTool:
    """Минимальный READ_ONLY tool — не трогает БД/Redis, просто отвечает."""

    name = _TOOL_NAME
    schema = {
        "name": _TOOL_NAME,
        "description": "test probe",
        "input_schema": {"type": "object", "properties": {}},
    }
    risk_level = RiskLevel.READ_ONLY

    async def run(self, ctx: ToolContext, args: dict) -> str:
        return "ok"


class _BrokenRedis:
    """Redis, который рейзит на любом вызове (сбой канала)."""

    async def incr(self, key: str) -> int:
        raise RuntimeError("redis down")

    async def expire(self, key: str, ttl: int) -> None:
        raise RuntimeError("redis down")


@pytest.fixture(autouse=True)
def _register_probe_tool():
    """Регистрирует одноразовый echo-tool на время теста, снимает после."""
    GLOBAL_REGISTRY.register(_EchoTool())
    _reset_memory_fallback_for_tests()
    yield
    GLOBAL_REGISTRY.unregister(_TOOL_NAME)
    _reset_memory_fallback_for_tests()


def _ctx_manager(redis_client: object | None) -> MCPContextManager:
    mgr = MCPContextManager()
    mgr.engine = MagicMock(name="engine")
    mgr.redis_client = redis_client
    mgr.meta_api_client = None
    return mgr


async def _call(mgr: MCPContextManager, *, times: int) -> list:
    """Вызывает call_tool N раз, возвращает список текстов последнего ответа каждого раза."""
    server = build_server(mgr)
    # low-level Server не выставляет call_tool напрямую как атрибут — достаём
    # зарегистрированный handler через request_handlers (mcp.server.lowlevel API).
    from mcp.types import CallToolRequest, CallToolRequestParams

    handler = server.request_handlers[CallToolRequest]
    results = []
    for _ in range(times):
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=_TOOL_NAME, arguments={}),
        )
        resp = await handler(req)
        results.append(resp)
    return results


def _is_rate_limited(resp) -> bool:
    """True если ответ — rate-limit отказ (текст начинается с '⏱')."""
    root = getattr(resp, "root", resp)
    content = getattr(root, "content", None) or []
    return any(getattr(c, "text", "").startswith("⏱") for c in content)


# Redis мёртв (redis_client=None, как при сбое старта MCPContextManager) — после
# исчерпания in-memory cap (5/60с) N+1-й вызов должен реджектиться, НЕ проходить бесконечно.
@pytest.mark.asyncio
async def test_call_tool_no_redis_client_uses_memory_cap_not_failopen() -> None:
    mgr = _ctx_manager(redis_client=None)
    responses = await _call(mgr, times=6)

    # Первые 5 — успех, 6-й — reject (in-memory cap = 5/60с).
    assert not any(_is_rate_limited(r) for r in responses[:5])
    assert _is_rate_limited(responses[5])


# Redis отвечает исключением на каждый вызов (не None, а живой битый клиент) —
# та же in-memory защита должна сработать, а не "поймали Exception и пропустили".
@pytest.mark.asyncio
async def test_call_tool_broken_redis_uses_memory_cap_not_failopen() -> None:
    mgr = _ctx_manager(redis_client=_BrokenRedis())
    responses = await _call(mgr, times=6)

    assert not any(_is_rate_limited(r) for r in responses[:5])
    assert _is_rate_limited(responses[5])


# Здоровый Redis, лимит НЕ исчерпан — check_and_increment вызывается штатно,
# инструмент отрабатывает без rate-limit отказа.
@pytest.mark.asyncio
async def test_call_tool_healthy_redis_not_rate_limited() -> None:
    fake_redis = AsyncMock()
    fake_redis.incr = AsyncMock(return_value=1)
    fake_redis.expire = AsyncMock(return_value=True)
    mgr = _ctx_manager(redis_client=fake_redis)

    responses = await _call(mgr, times=1)
    assert not _is_rate_limited(responses[0])
    fake_redis.incr.assert_awaited()
