from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from apps.api.schemas.common import SessionStatus


class BrowserSessionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    profile_id: str
    browser_host_id: str
    status: SessionStatus
    cdp_url: str | None = None
    webdriver_url: str | None = None
    last_started_at: datetime | None = None
    last_stopped_at: datetime | None = None
    last_message: str | None = None


class SessionControlRequest(BaseModel):
    browser_host_id: str = Field(min_length=1)
    reason: str = Field(default="Запрос оператора", min_length=1)


class SessionActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message: str
    session: BrowserSessionItem
