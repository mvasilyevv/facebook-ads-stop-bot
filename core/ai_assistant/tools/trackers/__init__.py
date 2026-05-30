# -*- coding: utf-8 -*-
"""READ_ONLY tools поверх трекера AdSet.pro (post-click статистика).

Импорт пакета регистрирует tool-классы в GLOBAL_REGISTRY (side-effect).
"""

from __future__ import annotations

from core.ai_assistant.tools.registry import GLOBAL_REGISTRY
from core.ai_assistant.tools.trackers.get_tracker_stats import GetTrackerStatsTool

GLOBAL_REGISTRY.register(GetTrackerStatsTool())

__all__ = ["GetTrackerStatsTool"]
