# -*- coding: utf-8 -*-
"""Singleton-конфиги системы: observer, vision, telegram, system key-value."""

from __future__ import annotations

from core.models.settings.observer_config import ObserverConfig
from core.models.settings.system_config import SystemConfig
from core.models.settings.telegram_config import TelegramConfig
from core.models.settings.vision_config import VisionConfig

__all__ = [
    "ObserverConfig",
    "SystemConfig",
    "TelegramConfig",
    "VisionConfig",
]
