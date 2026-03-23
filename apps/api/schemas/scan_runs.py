from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.api.schemas.common import ScanPipelineKind


class ScanRunItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    browser_host_id: str
    profile_id: str
    profile_launch_id: str | None = None
    profile_launch_name: str | None = None
    status: str
    pipeline_kind: ScanPipelineKind = ScanPipelineKind.FULL_SCAN
    trigger_source: str = "scheduler"
    target_fb_ad_ids: list[str] = Field(default_factory=list)
    rows_seen: int
    rows_parsed: int
    collect_ms: int = 0
    evaluate_ms: int = 0
    persist_ms: int = 0
    queue_ms: int = 0
    action_jobs_enqueued: int = 0
    scope_summary: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
