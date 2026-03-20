from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.browser_host.adapters.models import AutomationLaunchResult


@dataclass(slots=True, frozen=True)
class AttachedBrowserSession:
    profile_id: str
    cdp_url: str | None
    webdriver_url: str | None
    is_attached: bool


class PlaywrightAttachService:
    """Подготавливает подключение к браузеру через CDP или WebDriver."""

    async def attach(self, launch_result: AutomationLaunchResult) -> AttachedBrowserSession:
        logger = logging.getLogger(__name__)
        if not launch_result.cdp_url and not launch_result.webdriver_url:
            raise RuntimeError("Browser host не получил ни CDP, ни WebDriver endpoint")

        logger.info("Подключение к браузеру подготовлено для профиля %s", launch_result.profile_id)
        return AttachedBrowserSession(
            profile_id=launch_result.profile_id,
            cdp_url=launch_result.cdp_url,
            webdriver_url=launch_result.webdriver_url,
            is_attached=True,
        )
