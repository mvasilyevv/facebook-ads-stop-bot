# -*- coding: utf-8 -*-
"""Tracker domain: durable AdSet.pro inbox and click-state projection."""

from __future__ import annotations

from core.models.trackers.adsetpro_postback import AdsetProPostbackEvent
from core.models.trackers.click_state import TrackerClickState

__all__ = [
    "AdsetProPostbackEvent",
    "TrackerClickState",
]
