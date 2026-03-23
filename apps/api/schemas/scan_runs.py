from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ScanRunItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    browser_host_id: str
    profile_id: str
    profile_launch_id: str | None = None
    profile_launch_name: str | None = None
    status: str
    rows_seen: int
    rows_parsed: int
    scope_summary: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
