# -*- coding: utf-8 -*-
"""Публичный API пакета tools/.

Обратная совместимость с импортами вида:

    from core.ai_assistant.tools import TOOL_SCHEMAS, execute_tool, ToolError
    from core.ai_assistant.tools import ALLOWED_LOG_FILES, ALLOWED_SUPERVISOR_PROCESSES
"""

from __future__ import annotations

from core.ai_assistant.tools import creative as _creative  # noqa: F401
from core.ai_assistant.tools import drafts as _drafts  # noqa: F401

# Импорт meta регистрирует 5 READ_ONLY Meta Marketing API tools (side-effect import)
from core.ai_assistant.tools import meta as _meta  # noqa: F401

# Импорт ops регистрирует все 4 tools в GLOBAL_REGISTRY (side-effect import)
from core.ai_assistant.tools import ops as _ops  # noqa: F401
from core.ai_assistant.tools.base import RiskLevel, ToolError, ToolHandler
from core.ai_assistant.tools.ops.supervisor_restart import ALLOWED_SUPERVISOR_PROCESSES
from core.ai_assistant.tools.ops.tail_log import ALLOWED_LOG_FILES
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY, ToolRegistry

# Список схем — совместим со старым TOOL_SCHEMAS из tools.py
TOOL_SCHEMAS = GLOBAL_REGISTRY.schemas()


async def execute_tool(name: str, args: dict) -> str:
    """Обратно-совместимый wrapper. chat.py использует эту функцию.

    Логирует вызов и результат так же как старый execute_tool.
    """
    import logging

    logger = logging.getLogger(__name__)
    logger.info("AI tool invocation: %s args=%s", name, args)
    try:
        result = await GLOBAL_REGISTRY.execute(name, args)
        logger.info("AI tool %s OK", name)
        return result
    except ToolError:
        raise
    except Exception as exc:
        logger.exception("AI tool %s ошибка", name)
        raise ToolError(str(exc)) from exc


__all__ = [
    "TOOL_SCHEMAS",
    "execute_tool",
    "ToolError",
    "ToolHandler",
    "RiskLevel",
    "ToolRegistry",
    "GLOBAL_REGISTRY",
    # Обратная совместимость с тестами
    "ALLOWED_SUPERVISOR_PROCESSES",
    "ALLOWED_LOG_FILES",
]
