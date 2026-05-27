# -*- coding: utf-8 -*-
"""Singleton-конфиги системы: observer, vision, telegram, adsetpro, system key-value."""

from __future__ import annotations

from core.models.settings.adsetpro_credentials import AdsetProCredentials
from core.models.settings.observer_config import ObserverConfig
from core.models.settings.system_config import SystemConfig
from core.models.settings.telegram_config import TelegramConfig
from core.models.settings.vision_config import VisionConfig

__all__ = [
    "AdsetProCredentials",
    "ObserverConfig",
    "SystemConfig",
    "TelegramConfig",
    "VisionConfig",
]
