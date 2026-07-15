# -*- coding: utf-8 -*-
"""DRAFT_REQUIRED tools — создают task_queue draft через core.meta_api.queue.

Импорт пакета регистрирует tool-классы в GLOBAL_REGISTRY (side-effect).
"""

from __future__ import annotations

from core.ai_assistant.tools.drafts.request_budget_change import RequestBudgetChangeTool
from core.ai_assistant.tools.drafts.request_bulk_pause import RequestBulkPauseTool
from core.ai_assistant.tools.drafts.request_clone_campaign import RequestCloneCampaignTool
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

GLOBAL_REGISTRY.register(RequestBudgetChangeTool())
GLOBAL_REGISTRY.register(RequestCloneCampaignTool())
GLOBAL_REGISTRY.register(RequestBulkPauseTool())

__all__ = [
    "RequestBudgetChangeTool",
    "RequestBulkPauseTool",
    "RequestCloneCampaignTool",
]
