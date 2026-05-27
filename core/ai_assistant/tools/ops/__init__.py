# -*- coding: utf-8 -*-
"""READ_ONLY операционные tools — БД + Redis.

Импорт пакета регистрирует все 4 tool'а в GLOBAL_REGISTRY (side-effect).
"""

from __future__ import annotations

from core.ai_assistant.tools.ops.get_active_offers import GetActiveOffersTool
from core.ai_assistant.tools.ops.get_disable_tasks_status import GetDisableTasksStatusTool
from core.ai_assistant.tools.ops.get_recent_alerts import GetRecentAlertsTool
from core.ai_assistant.tools.ops.get_worker_health import GetWorkerHealthTool
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

GLOBAL_REGISTRY.register(GetActiveOffersTool())
GLOBAL_REGISTRY.register(GetRecentAlertsTool())
GLOBAL_REGISTRY.register(GetDisableTasksStatusTool())
GLOBAL_REGISTRY.register(GetWorkerHealthTool())

__all__ = [
    "GetActiveOffersTool",
    "GetDisableTasksStatusTool",
    "GetRecentAlertsTool",
    "GetWorkerHealthTool",
]
