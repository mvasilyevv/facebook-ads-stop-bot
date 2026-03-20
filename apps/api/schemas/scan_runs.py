from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ScanRunItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    browser_host_id: str
    profile_id: str
    status: str
    rows_seen: int
    rows_parsed: int
    scope_summary: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
