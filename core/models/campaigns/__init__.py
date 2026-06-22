# -*- coding: utf-8 -*-
"""Campaigns-домен: пресеты и запуски создания FB-кампаний.

См. дизайн docs/superpowers/specs/2026-06-22-campaign-creation-service-design.md.
"""

from __future__ import annotations

from core.models.campaigns.preset import CampaignPreset
from core.models.campaigns.run import CAMPAIGN_RUN_STATUSES, CampaignRun

__all__ = [
    "CAMPAIGN_RUN_STATUSES",
    "CampaignPreset",
    "CampaignRun",
]
