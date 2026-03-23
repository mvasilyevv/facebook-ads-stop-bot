from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProfileLaunchItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    profile_id: str
    display_name: str
    browser_host_id: str
    name: str
    is_active: bool
    started_at: datetime
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProfileLaunchCreateRequest(BaseModel):
    profile_id: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=255)


class ProfileLaunchRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ProfileLaunchActionResponse(BaseModel):
    message: str
    launch: ProfileLaunchItem
    cleared_control_flags: int = 0
    cleared_cooldowns: int = 0


class ProfileLaunchDashboardSummaryItem(BaseModel):
    total_ads: int
    active_ads: int
    paused_ads: int
    attention_ads: int
    spend_total: Decimal
    scans_count: int
    last_scan_at: datetime | None = None


class ProfileLaunchTrendPointItem(BaseModel):
    timestamp: datetime
    value: Decimal


class ProfileLaunchDashboardResponse(BaseModel):
    launch: ProfileLaunchItem
    previous_launch: ProfileLaunchItem | None = None
    current: ProfileLaunchDashboardSummaryItem
    previous: ProfileLaunchDashboardSummaryItem | None = None
    spend_series: list[ProfileLaunchTrendPointItem]
    attention_series: list[ProfileLaunchTrendPointItem]
    action_series: list[ProfileLaunchTrendPointItem]
