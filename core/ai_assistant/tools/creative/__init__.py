# -*- coding: utf-8 -*-
"""CREATIVE tools — LLM-генерация без mutation.

При импорте этого пакета оба tools регистрируются в GLOBAL_REGISTRY.
"""

from __future__ import annotations

from core.ai_assistant.tools.creative.analyze_creative import AnalyzeCreativeTool
from core.ai_assistant.tools.creative.generate_ad_copy import GenerateAdCopyTool
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

# Регистрируем экземпляры CREATIVE tools
GLOBAL_REGISTRY.register(GenerateAdCopyTool())
GLOBAL_REGISTRY.register(AnalyzeCreativeTool())

__all__ = [
    "GenerateAdCopyTool",
    "AnalyzeCreativeTool",
]
