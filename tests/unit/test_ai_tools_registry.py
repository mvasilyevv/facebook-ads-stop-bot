# -*- coding: utf-8 -*-
"""Unit-тесты ToolRegistry и ToolContext.

Покрытие:
- register: уникальность имени, дубль → ValueError.
- list_names / list_by_risk / schemas — корректные срезы.
- execute: успешный путь, ToolError, неизвестный tool, непредвиденная ошибка.
- ToolContext.require_engine/require_meta_api/require_redis — ToolError на None.
- _ratelimit.check_and_increment fail-open и cap.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.ai_assistant.tools._ratelimit import (
    RateLimitExceeded,
    check_and_increment,
)
from core.ai_assistant.tools.base import (
    RiskLevel,
    ToolContext,
    ToolError,
)
from core.ai_assistant.tools.registry import ToolRegistry


class _FakeTool:
    """Лёгкая реализация ToolHandler-протокола для unit-тестов."""

    def __init__(
        self,
        name: str,
        risk_level: RiskLevel,
        *,
        result: str = "ok",
        raise_tool_error: bool = False,
        raise_runtime: bool = False,
    ) -> None:
        self.name = name
        self.risk_level = risk_level
        self.schema = {"name": name, "description": f"{name} fake", "input_schema": {}}
        self._result = result
        self._raise_tool_error = raise_tool_error
        self._raise_runtime = raise_runtime
        self.called_with: tuple[ToolContext, dict[str, Any]] | None = None

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        self.called_with = (ctx, args)
        if self._raise_tool_error:
            raise ToolError("ожидаемая ошибка")
        if self._raise_runtime:
            raise RuntimeError("неожиданная ошибка")
        return self._result


# Регистрация в чистый registry проходит, дубль имени → ValueError.
def test_register_unique_and_duplicate() -> None:
    reg = ToolRegistry()
    reg.register(_FakeTool("alpha", RiskLevel.READ_ONLY))
    reg.register(_FakeTool("beta", RiskLevel.DRAFT_REQUIRED))
    assert reg.list_names() == ["alpha", "beta"]

    with pytest.raises(ValueError):
        reg.register(_FakeTool("alpha", RiskLevel.CREATIVE))


# unregister снимает регистрацию и не падает на отсутствующем имени.
def test_unregister_and_get() -> None:
    reg = ToolRegistry()
    reg.register(_FakeTool("alpha", RiskLevel.READ_ONLY))
    reg.unregister("alpha")
    reg.unregister("ghost")  # no-op
    assert reg.get("alpha") is None
    assert reg.list_names() == []


# list_by_risk должен отдавать только tools соответствующей категории.
def test_list_by_risk_splits_correctly() -> None:
    reg = ToolRegistry()
    a = _FakeTool("a", RiskLevel.READ_ONLY)
    b = _FakeTool("b", RiskLevel.DRAFT_REQUIRED)
    c = _FakeTool("c", RiskLevel.READ_ONLY)
    reg.register(a)
    reg.register(b)
    reg.register(c)

    read_only = reg.list_by_risk(RiskLevel.READ_ONLY)
    drafts = reg.list_by_risk(RiskLevel.DRAFT_REQUIRED)
    assert sorted(t.name for t in read_only) == ["a", "c"]
    assert [t.name for t in drafts] == ["b"]


# schemas() возвращает плоский список схем (по числу tools).
def test_schemas_returns_list() -> None:
    reg = ToolRegistry()
    reg.register(_FakeTool("a", RiskLevel.READ_ONLY))
    reg.register(_FakeTool("b", RiskLevel.DRAFT_REQUIRED))
    schemas = reg.schemas()
    assert len(schemas) == 2
    assert {s["name"] for s in schemas} == {"a", "b"}


# Успешный execute возвращает результат tool'а и прокидывает ctx + args в run.
@pytest.mark.asyncio
async def test_execute_success_passes_ctx_and_args() -> None:
    reg = ToolRegistry()
    tool = _FakeTool("alpha", RiskLevel.READ_ONLY, result="hello")
    reg.register(tool)
    ctx = ToolContext(client_key="user-1")
    result = await reg.execute("alpha", {"k": "v"}, ctx)
    assert result == "hello"
    assert tool.called_with == (ctx, {"k": "v"})


# Если tool сам бросает ToolError — registry прокидывает её без обёртки.
@pytest.mark.asyncio
async def test_execute_propagates_tool_error() -> None:
    reg = ToolRegistry()
    reg.register(_FakeTool("bad", RiskLevel.READ_ONLY, raise_tool_error=True))
    ctx = ToolContext(client_key="u")
    with pytest.raises(ToolError, match="ожидаемая ошибка"):
        await reg.execute("bad", {}, ctx)


# Непредвиденная ошибка → оборачивается в ToolError, чтобы LLM не упал.
@pytest.mark.asyncio
async def test_execute_wraps_unexpected_exception() -> None:
    reg = ToolRegistry()
    reg.register(_FakeTool("boom", RiskLevel.READ_ONLY, raise_runtime=True))
    ctx = ToolContext(client_key="u")
    with pytest.raises(ToolError, match="Внутренняя ошибка tool 'boom'"):
        await reg.execute("boom", {}, ctx)


# Неизвестный tool → ToolError с пояснением.
@pytest.mark.asyncio
async def test_execute_unknown_tool() -> None:
    reg = ToolRegistry()
    ctx = ToolContext(client_key="u")
    with pytest.raises(ToolError, match="Неизвестный tool"):
        await reg.execute("absent", {}, ctx)


# require_* должны бросать ToolError если соответствующее поле ctx не задано.
def test_tool_context_require_methods() -> None:
    ctx = ToolContext(client_key="u")
    with pytest.raises(ToolError, match="engine"):
        ctx.require_engine()
    with pytest.raises(ToolError, match="meta_api_client"):
        ctx.require_meta_api()
    with pytest.raises(ToolError, match="redis_client"):
        ctx.require_redis()


# effective_requested_by формирует ai:<key> если поле явно не задано.
def test_tool_context_effective_requested_by_default() -> None:
    ctx = ToolContext(client_key="user-42")
    assert ctx.effective_requested_by() == "ai:user-42"
    ctx_explicit = ToolContext(client_key="user-42", requested_by="tg:bob")
    assert ctx_explicit.effective_requested_by() == "tg:bob"


# Без redis_client check_and_increment должен fail-open (вернуть 0, не бросать).
@pytest.mark.asyncio
async def test_ratelimit_fail_open_without_redis() -> None:
    counter = await check_and_increment(None, client_key="u", max_per_hour=10)
    assert counter == 0


# Через fakeredis: пять вызовов в пределах лимита, шестой → RateLimitExceeded.
@pytest.mark.asyncio
async def test_ratelimit_cap_via_fakeredis() -> None:
    try:
        import fakeredis.aioredis  # type: ignore
    except ImportError:
        pytest.skip("fakeredis не установлен")
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        for _ in range(5):
            await check_and_increment(r, client_key="kk", max_per_hour=5)
        with pytest.raises(RateLimitExceeded):
            await check_and_increment(r, client_key="kk", max_per_hour=5)
    finally:
        await r.aclose()
