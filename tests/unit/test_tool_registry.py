# -*- coding: utf-8 -*-
"""Тесты для ToolRegistry и инфраструктуры пакета tools/."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.ai_assistant.tools.base import RiskLevel, ToolError, ToolHandler
from core.ai_assistant.tools.registry import ToolRegistry

# --- Вспомогательные фикстуры ---


def _make_tool(name: str, risk: RiskLevel = RiskLevel.READ_ONLY) -> ToolHandler:
    """Создать минимальный mock tool для тестирования."""

    class _FakeTool:
        pass

    t = _FakeTool()
    t.name = name  # type: ignore[attr-defined]
    t.risk_level = risk  # type: ignore[attr-defined]
    t.schema = {"name": name, "description": f"Tool {name}"}  # type: ignore[attr-defined]
    t.run = AsyncMock(return_value=f"result_{name}")  # type: ignore[attr-defined]
    return t  # type: ignore[return-value]


# Сценарий: регистрация нового tool сохраняет его в реестре.
def test_registry_register_adds_tool() -> None:
    reg = ToolRegistry()
    tool = _make_tool("test_tool")
    reg.register(tool)
    assert reg.get("test_tool") is tool


# Сценарий: повторная регистрация с тем же именем → ValueError.
def test_registry_register_duplicate_raises() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("dup"))
    with pytest.raises(ValueError, match="уже зарегистрирован"):
        reg.register(_make_tool("dup"))


# Сценарий: get несуществующего tool возвращает None.
def test_registry_get_unknown_returns_none() -> None:
    reg = ToolRegistry()
    assert reg.get("nonexistent") is None


# Сценарий: list_names возвращает отсортированный список.
def test_registry_list_names_sorted() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("zzz"))
    reg.register(_make_tool("aaa"))
    reg.register(_make_tool("mmm"))
    assert reg.list_names() == ["aaa", "mmm", "zzz"]


# Сценарий: list_by_risk фильтрует tools по уровню риска.
def test_registry_list_by_risk() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("read_tool", RiskLevel.READ_ONLY))
    reg.register(_make_tool("draft_tool", RiskLevel.DRAFT_REQUIRED))
    reg.register(_make_tool("creative_tool", RiskLevel.CREATIVE))

    read_only = reg.list_by_risk(RiskLevel.READ_ONLY)
    draft = reg.list_by_risk(RiskLevel.DRAFT_REQUIRED)
    creative = reg.list_by_risk(RiskLevel.CREATIVE)

    assert len(read_only) == 1 and read_only[0].name == "read_tool"
    assert len(draft) == 1 and draft[0].name == "draft_tool"
    assert len(creative) == 1 and creative[0].name == "creative_tool"


# Сценарий: schemas возвращает список dict с полем name для каждого tool.
def test_registry_schemas_returns_list() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("a"))
    reg.register(_make_tool("b"))
    schemas = reg.schemas()
    assert len(schemas) == 2
    names = {s["name"] for s in schemas}
    assert names == {"a", "b"}


# Сценарий: execute неизвестного tool → ToolError.
@pytest.mark.asyncio
async def test_registry_execute_unknown_raises_tool_error() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolError, match="Неизвестный tool"):
        await reg.execute("nonexistent", {})


# Сценарий: execute tool который падает с RuntimeError → ToolError с фразой "Внутренняя ошибка".
@pytest.mark.asyncio
async def test_registry_execute_crashing_tool_wraps_as_tool_error() -> None:
    reg = ToolRegistry()
    tool = _make_tool("crash_tool")
    tool.run = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[attr-defined]
    reg.register(tool)
    with pytest.raises(ToolError, match="Внутренняя ошибка"):
        await reg.execute("crash_tool", {})


# Сценарий: execute tool который сам поднимает ToolError → ToolError пробрасывается без обёртки.
@pytest.mark.asyncio
async def test_registry_execute_tool_error_propagated() -> None:
    reg = ToolRegistry()
    tool = _make_tool("err_tool")
    tool.run = AsyncMock(side_effect=ToolError("специфическая ошибка"))  # type: ignore[attr-defined]
    reg.register(tool)
    with pytest.raises(ToolError, match="специфическая ошибка"):
        await reg.execute("err_tool", {})


# Сценарий: при импорте core.ai_assistant.tools все 4 ops-tools зарегистрированы в GLOBAL_REGISTRY.
def test_global_registry_has_all_ops_tools() -> None:
    from core.ai_assistant.tools import GLOBAL_REGISTRY

    names = set(GLOBAL_REGISTRY.list_names())
    assert "supervisor_restart" in names
    assert "tail_log" in names
    assert "api_get" in names
    assert "set_scanning" in names
    assert len(names) >= 4


# Сценарий: RiskLevel enum содержит ожидаемые значения.
def test_risk_level_enum_values() -> None:
    assert RiskLevel.READ_ONLY == "read_only"
    assert RiskLevel.DRAFT_REQUIRED == "draft_required"
    assert RiskLevel.CREATIVE == "creative"


# Сценарий: TOOL_SCHEMAS содержит ровно 4 элемента с именами ops-tools.
def test_tool_schemas_count() -> None:
    from core.ai_assistant.tools import TOOL_SCHEMAS

    assert len(TOOL_SCHEMAS) >= 4
    schema_names = {s["name"] for s in TOOL_SCHEMAS}
    assert {"supervisor_restart", "tail_log", "api_get", "set_scanning"}.issubset(schema_names)
