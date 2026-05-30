# -*- coding: utf-8 -*-
"""Публичный API пакета core.ai_assistant.tools.

Импорт пакета регистрирует все tool-классы в GLOBAL_REGISTRY (side-effect import
modules ops/meta/drafts/creative).

Основные сущности:
- GLOBAL_REGISTRY (ToolRegistry) — реестр всех зарегистрированных tool'ов.
- ToolHandler / ToolContext / RiskLevel / ToolError — контракт.
- execute_tool(name, args, ctx) — точка вызова, используется chat.py.
- check_rate_limit(ctx) — проверка ai:ratelimit:* в Redis перед раундом tool-use.
"""

from __future__ import annotations

import logging
from typing import Any

from core.ai_assistant.tools import creative as _creative  # noqa: F401, E402
from core.ai_assistant.tools import drafts as _drafts  # noqa: F401, E402
from core.ai_assistant.tools import meta as _meta  # noqa: F401, E402

# Side-effect импорты — регистрируют tool-классы в GLOBAL_REGISTRY.
# Порядок не важен, но удобно поддерживать категории читаемо.
from core.ai_assistant.tools import ops as _ops  # noqa: F401, E402
from core.ai_assistant.tools import trackers as _trackers  # noqa: F401, E402
from core.ai_assistant.tools._ratelimit import (
    RateLimitExceeded,
    check_and_increment,
)
from core.ai_assistant.tools.base import (
    RiskLevel,
    ToolContext,
    ToolError,
    ToolHandler,
)
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY, ToolRegistry

logger = logging.getLogger(__name__)


async def execute_tool(
    name: str,
    args: dict[str, Any],
    ctx: ToolContext,
) -> str:
    """Точка вызова — обёртка над GLOBAL_REGISTRY.execute с логированием.

    Используется ChatSession для всех tool-use раундов.
    """
    logger.info(
        "AI tool invocation: name=%s client_key=%s args=%s",
        name,
        ctx.client_key,
        args,
    )
    try:
        result = await GLOBAL_REGISTRY.execute(name, args, ctx)
        logger.info("AI tool %s OK (len=%d)", name, len(result))
        return result
    except ToolError:
        raise
    except Exception as exc:
        logger.exception("AI tool %s упал", name)
        raise ToolError(str(exc)) from exc


async def check_rate_limit(ctx: ToolContext, *, max_per_hour: int = 30) -> None:
    """Проверить ai:ratelimit:* для client_key.

    Бросает ToolError(RateLimitExceeded) если лимит превышен.
    Fail-open если redis недоступен — внутри check_and_increment.
    """
    if ctx.redis_client is None:
        return
    try:
        await check_and_increment(
            ctx.redis_client,
            client_key=ctx.client_key,
            max_per_hour=max_per_hour,
        )
    except RateLimitExceeded as exc:
        raise ToolError(str(exc)) from exc


TOOL_SCHEMAS: list[dict[str, Any]] = GLOBAL_REGISTRY.schemas()
"""Снимок схем на момент импорта. ChatSession читает GLOBAL_REGISTRY.schemas() на лету."""


__all__ = [
    "GLOBAL_REGISTRY",
    "RateLimitExceeded",
    "RiskLevel",
    "TOOL_SCHEMAS",
    "ToolContext",
    "ToolError",
    "ToolHandler",
    "ToolRegistry",
    "check_rate_limit",
    "execute_tool",
]
