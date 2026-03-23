from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from apps.api.schemas.common import FastStopState, RiskBand


class WatchlistItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    fb_ad_id: str
    profile_id: str | None = None
    browser_host_id: str | None = None
    campaign_name: str | None = None
    adset_name: str | None = None
    ad_name: str | None = None
    risk_band: RiskBand
    fast_stop_state: FastStopState
    watch_reason: str | None = None
    priority_score: int
    next_check_at: datetime
    last_metrics_at: datetime | None = None
    attempt_count: int
    source_scan_run_id: str | None = None
