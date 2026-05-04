# -*- coding: utf-8 -*-
"""Тесты whitelist для AI-инструментов."""

from __future__ import annotations

import pytest

from core.ai_assistant.tools import (
    ALLOWED_LOG_FILES,
    ALLOWED_SUPERVISOR_PROCESSES,
    ToolError,
    execute_tool,
)


# Сценарий: попытка перезапустить процесс не из whitelist должна быть отклонена.
@pytest.mark.asyncio
async def test_supervisor_restart_rejects_non_whitelisted():
    with pytest.raises(ToolError):
        await execute_tool("supervisor_restart", {"process": "postgres"})


# Сценарий: tail_log с неизвестным именем лога → ToolError.
@pytest.mark.asyncio
async def test_tail_log_rejects_non_whitelisted():
    with pytest.raises(ToolError):
        await execute_tool("tail_log", {"log_name": "../../etc/passwd"})


# Сценарий: api_get отвергает не-/api/ путь.
@pytest.mark.asyncio
async def test_api_get_rejects_non_api_path():
    with pytest.raises(ToolError):
        await execute_tool("api_get", {"path": "/admin/secrets"})


# Сценарий: api_get отвергает path с traversal.
@pytest.mark.asyncio
async def test_api_get_rejects_path_traversal():
    with pytest.raises(ToolError):
        await execute_tool("api_get", {"path": "/api/../../admin"})


# Сценарий: неизвестный tool → ToolError.
@pytest.mark.asyncio
async def test_unknown_tool_rejected():
    with pytest.raises(ToolError):
        await execute_tool("rm_rf", {"path": "/"})


# Сценарий: whitelist содержит ожидаемые элементы (защита от случайного удаления).
def test_whitelist_contains_core_workers():
    assert "observer_worker" in ALLOWED_SUPERVISOR_PROCESSES
    assert "browser_agent" in ALLOWED_SUPERVISOR_PROCESSES
    assert "observer.log" in ALLOWED_LOG_FILES
    assert "supervisord.log" in ALLOWED_LOG_FILES
