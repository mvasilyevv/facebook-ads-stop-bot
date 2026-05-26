# -*- coding: utf-8 -*-
"""READ_ONLY tools поверх Marketing API.

При импорте этого пакета все 5 tools регистрируются в GLOBAL_REGISTRY (side-effect import).
"""

from core.ai_assistant.tools.meta.find_ads import FindAdsTool
from core.ai_assistant.tools.meta.get_account_health import GetAccountHealthTool
from core.ai_assistant.tools.meta.get_competitor_patterns import GetCompetitorPatternsTool
from core.ai_assistant.tools.meta.get_insights import GetInsightsTool
from core.ai_assistant.tools.meta.get_offer_performance import GetOfferPerformanceTool
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

# Регистрируем все 5 экземпляров meta READ_ONLY tools
GLOBAL_REGISTRY.register(GetInsightsTool())
GLOBAL_REGISTRY.register(FindAdsTool())
GLOBAL_REGISTRY.register(GetOfferPerformanceTool())
GLOBAL_REGISTRY.register(GetAccountHealthTool())
GLOBAL_REGISTRY.register(GetCompetitorPatternsTool())

__all__ = [
    "GetInsightsTool",
    "FindAdsTool",
    "GetOfferPerformanceTool",
    "GetAccountHealthTool",
    "GetCompetitorPatternsTool",
]
