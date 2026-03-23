from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProfileItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    profile_id: str
    display_name: str
    browser_host_id: str
    is_active: bool
    scan_suspended: bool
    last_launch_at: datetime | None = None
