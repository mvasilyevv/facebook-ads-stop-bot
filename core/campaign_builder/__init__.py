# -*- coding: utf-8 -*-
"""core.campaign_builder — переиспользуемый движок создания FB-кампаний.

Извлечён из scripts/fb_launch.py: pydantic-конфиг CampaignConfig + чистая
build_campaign_spec (план объектов для dry-run/validate/воркера). CLI fb_launch
и будущий campaign_creator_worker импортируют отсюда — без форка логики.
"""

from __future__ import annotations

from core.campaign_builder.builder import (
    EXEC_STEP_ORDER,
    AdsetSpec,
    AdSpec,
    CampaignSpec,
    CampaignSpec_Block,
    ExecStep,
    ad_body,
    adset_body,
    build_campaign_spec,
    campaign_body,
    image_creative_body,
    plan_execution_steps,
    url_tags_of,
    video_creative_body,
)
from core.campaign_builder.config import (
    Account,
    AdsetConfig,
    AdText,
    Attribution,
    Budget,
    CampaignBlock,
    CampaignConfig,
    LaunchState,
    Targeting,
)
from core.campaign_builder.naming import creative_codes, render_name

__all__ = [
    # config
    "Account",
    "AdText",
    "AdsetConfig",
    "Attribution",
    "Budget",
    "CampaignBlock",
    "CampaignConfig",
    "LaunchState",
    "Targeting",
    # naming
    "creative_codes",
    "render_name",
    # builder
    "EXEC_STEP_ORDER",
    "AdSpec",
    "AdsetSpec",
    "CampaignSpec",
    "CampaignSpec_Block",
    "ExecStep",
    "ad_body",
    "adset_body",
    "build_campaign_spec",
    "campaign_body",
    "image_creative_body",
    "plan_execution_steps",
    "url_tags_of",
    "video_creative_body",
]
