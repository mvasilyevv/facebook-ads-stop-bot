from __future__ import annotations

import logging

from apps.browser_host.adapters.base import AntiDetectAdapter
from apps.browser_host.adapters.models import AdapterHealth
from apps.browser_host.playwright_attach import AttachedBrowserSession, PlaywrightAttachService


class BrowserSessionManager:
    """Управляет жизненным циклом browser host сессии."""

    def __init__(
        self,
        adapter: AntiDetectAdapter,
        playwright_attach_service: PlaywrightAttachService,
    ) -> None:
        self._adapter = adapter
        self._playwright_attach_service = playwright_attach_service

    async def healthcheck(self) -> AdapterHealth:
        return await self._adapter.healthcheck()

    async def ensure_session(
        self,
        profile_id: str,
        launch_mode: str = "cdp",
        launch_args: list[str] | None = None,
    ) -> AttachedBrowserSession:
        logger = logging.getLogger(__name__)
        logger.info("Запрашиваю automation-сессию для профиля %s", profile_id)
        await self._adapter.ensure_single_active_profile()
        status = await self._adapter.get_profile_status(profile_id)
        if status.has_automation_binding:
            logger.info("Профиль %s уже готов к автоматизации", profile_id)
        else:
            logger.info("Профиль %s будет перезапущен с флагами автоматизации", profile_id)
            await self._adapter.stop_profile(profile_id)

        launch_result = await self._adapter.start_profile_for_automation(
            profile_id=profile_id,
            launch_mode=launch_mode,
            launch_args=launch_args or [],
        )
        return await self._playwright_attach_service.attach(launch_result)
