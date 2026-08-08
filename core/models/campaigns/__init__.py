# -*- coding: utf-8 -*-
"""Campaigns-домен: пресеты и запуски создания FB-кампаний."""

from __future__ import annotations

from core.models.campaigns.creative_seq import CampaignCreative, OfferCreativeSeq
from core.models.campaigns.preset import CampaignPreset
from core.models.campaigns.run import CAMPAIGN_RUN_STATUSES, CampaignRun

__all__ = [
    "CAMPAIGN_RUN_STATUSES",
    "CampaignCreative",
    "CampaignPreset",
    "CampaignRun",
    "OfferCreativeSeq",
]
