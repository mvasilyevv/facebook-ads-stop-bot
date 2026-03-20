from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True, frozen=True)
class ProfileInfo:
    profile_id: str
    display_name: str
    is_active: bool


@dataclass(slots=True, frozen=True)
class OpenProfileInfo:
    profile_id: str
    display_name: str
    debug_endpoint: str | None = None


@dataclass(slots=True, frozen=True)
class ProfileStatus:
    profile_id: str
    state: str
    has_automation_binding: bool


@dataclass(slots=True, frozen=True)
class AutomationLaunchResult:
    profile_id: str
    vendor: str
    cdp_url: str | None
    webdriver_url: str | None
    debug_port: int | None
    browser_pid: int | None
    launched_at: datetime


@dataclass(slots=True, frozen=True)
class AdapterHealth:
    is_healthy: bool
    message: str
