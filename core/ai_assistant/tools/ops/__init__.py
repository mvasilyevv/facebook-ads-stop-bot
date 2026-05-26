# -*- coding: utf-8 -*-
"""Существующие операционные tools — миграция из старого tools.py.

При импорте этого пакета все четыре tools регистрируются в GLOBAL_REGISTRY.
"""

from core.ai_assistant.tools.ops.api_get import ApiGetTool
from core.ai_assistant.tools.ops.set_scanning import SetScanningTool
from core.ai_assistant.tools.ops.supervisor_restart import SupervisorRestartTool
from core.ai_assistant.tools.ops.tail_log import TailLogTool
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

# Регистрируем экземпляры всех ops tools
GLOBAL_REGISTRY.register(SupervisorRestartTool())
GLOBAL_REGISTRY.register(TailLogTool())
GLOBAL_REGISTRY.register(ApiGetTool())
GLOBAL_REGISTRY.register(SetScanningTool())

__all__ = [
    "SupervisorRestartTool",
    "TailLogTool",
    "ApiGetTool",
    "SetScanningTool",
]
