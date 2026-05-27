# -*- coding: utf-8 -*-
"""Trackers домен: AdsetPro postback'и + агрегаты."""

from __future__ import annotations

from core.models.trackers.aggregate import TrackerAggregate
from core.models.trackers.postback import TrackerPostback

__all__ = [
    "TrackerAggregate",
    "TrackerPostback",
]
