# -*- coding: utf-8 -*-
"""READ_ONLY tools поверх MetaApiClient (Marketing API).

Импорт пакета регистрирует tool-классы в GLOBAL_REGISTRY (side-effect).
"""

from __future__ import annotations

from core.ai_assistant.tools.meta.find_ads import FindAdsTool
from core.ai_assistant.tools.meta.get_account_health import GetAccountHealthTool
from core.ai_assistant.tools.meta.get_insights import GetInsightsTool
from core.ai_assistant.tools.meta.get_offer_performance import GetOfferPerformanceTool
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

GLOBAL_REGISTRY.register(GetInsightsTool())
GLOBAL_REGISTRY.register(FindAdsTool())
GLOBAL_REGISTRY.register(GetOfferPerformanceTool())
GLOBAL_REGISTRY.register(GetAccountHealthTool())

__all__ = [
    "FindAdsTool",
    "GetAccountHealthTool",
    "GetInsightsTool",
    "GetOfferPerformanceTool",
]
