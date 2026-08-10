# -*- coding: utf-8 -*-
"""ToolRegistry — реестр зарегистрированных tools + dispatch.

Контракты:
- register(tool) — уникальное имя, поднимает ValueError если повтор.
- execute(name, args, ctx) — единая точка вызова, оборачивает исключения в ToolError.
- list_by_risk(level) — для UI/тестов.
- schemas() — список JSON Schema для передачи в LLM.

GLOBAL_REGISTRY заполняется side-effect импортами модулей ops/meta/creative.
"""

from __future__ import annotations

import logging
from typing import Any

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError, ToolHandler

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Хранит зарегистрированные tool-обработчики и диспатчит вызовы."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolHandler] = {}

    def register(self, tool: ToolHandler) -> None:
        """Зарегистрировать tool. ValueError если имя уже занято."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' уже зарегистрирован")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Снять регистрацию (используется в тестах)."""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolHandler | None:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_by_risk(self, risk_level: RiskLevel) -> list[ToolHandler]:
        return [t for t in self._tools.values() if t.risk_level == risk_level]

    def schemas(self) -> list[dict[str, Any]]:
        """JSON Schema каждого tool'а — для передачи в Anthropic/OpenAI как tools=."""
        return [t.schema for t in self._tools.values()]

    async def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> str:
        """Исполнить tool. ToolError проходит как есть, прочие — оборачиваются."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"Неизвестный tool: '{name}'")
        try:
            if tool.__class__.__module__.startswith("core.ai_assistant.tools.meta."):
                from core.tasks.browser_fence import (
                    BrowserFenceLeaseLost,
                    BrowserOperationBlocked,
                    BrowserOperationFence,
                )

                target = str(args.get("ad_account_id") or name).strip()[:128]
                try:
                    async with BrowserOperationFence(
                        ctx.require_engine(),
                        operation_kind="ai_meta_read",
                        target=target,
                    ) as fence:
                        result = await tool.run(ctx, args)
                        await fence.assert_held()
                        return result
                except BrowserOperationBlocked as exc:
                    raise ToolError(
                        "Vision maintenance is active; Marketing API read was not started"
                    ) from exc
                except BrowserFenceLeaseLost as exc:
                    raise ToolError(
                        "Marketing API read fence was lost; retry after reconciliation"
                    ) from exc
            return await tool.run(ctx, args)
        except ToolError:
            raise
        except Exception as exc:
            logger.exception("Tool '%s' упал с непредвиденной ошибкой", name)
            raise ToolError(f"Внутренняя ошибка tool '{name}': {exc}") from exc


GLOBAL_REGISTRY = ToolRegistry()
"""Заполняется при импорте subpackage'ей tools.ops / tools.meta / tools.creative."""
