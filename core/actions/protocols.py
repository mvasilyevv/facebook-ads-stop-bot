from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True, frozen=True)
class BrowserActionResult:
    """Структурированный результат browser action."""

    success: bool
    message: str
    fb_ad_id: str
    profile_id: str
    browser_host_name: str


class PauseAdExecutor(Protocol):
    """Контракт для отдельного исполнителя паузы объявления."""

    async def pause_ad(
        self,
        profile_id: str,
        browser_host_name: str,
        fb_ad_id: str,
    ) -> BrowserActionResult: ...


class ResumeAdExecutor(Protocol):
    """Контракт для отдельного исполнителя возобновления объявления."""

    async def resume_ad(
        self,
        profile_id: str,
        browser_host_name: str,
        fb_ad_id: str,
    ) -> BrowserActionResult: ...
