# -*- coding: utf-8 -*-
"""READ_ONLY operational tools backed by durable sources.

Импорт пакета регистрирует tool'ы в GLOBAL_REGISTRY (side-effect).
"""

from __future__ import annotations

from core.ai_assistant.tools.ops.get_active_offers import GetActiveOffersTool
from core.ai_assistant.tools.ops.get_ad_action_status import GetAdActionStatusTool
from core.ai_assistant.tools.ops.get_recent_alerts import GetRecentAlertsTool
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

GLOBAL_REGISTRY.register(GetActiveOffersTool())
GLOBAL_REGISTRY.register(GetRecentAlertsTool())
GLOBAL_REGISTRY.register(GetAdActionStatusTool())

__all__ = [
    "GetActiveOffersTool",
    "GetAdActionStatusTool",
    "GetRecentAlertsTool",
]
