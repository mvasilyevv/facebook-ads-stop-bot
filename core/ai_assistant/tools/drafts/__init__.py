# -*- coding: utf-8 -*-
"""DRAFT_REQUIRED tools — создают MetaApiMutationTask со status=DRAFT для подтверждения юзером.

При импорте этого пакета все четыре DRAFT-tools регистрируются в GLOBAL_REGISTRY.
"""

from core.ai_assistant.tools.drafts.request_budget_change import RequestBudgetChangeTool
from core.ai_assistant.tools.drafts.request_bulk_pause import RequestBulkPauseTool
from core.ai_assistant.tools.drafts.request_clone_campaign import RequestCloneCampaignTool
from core.ai_assistant.tools.drafts.request_create_campaign import RequestCreateCampaignTool
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

# Регистрируем экземпляры всех DRAFT_REQUIRED tools
GLOBAL_REGISTRY.register(RequestBudgetChangeTool())
GLOBAL_REGISTRY.register(RequestCloneCampaignTool())
GLOBAL_REGISTRY.register(RequestBulkPauseTool())
GLOBAL_REGISTRY.register(RequestCreateCampaignTool())

__all__ = [
    "RequestBudgetChangeTool",
    "RequestCloneCampaignTool",
    "RequestBulkPauseTool",
    "RequestCreateCampaignTool",
]
