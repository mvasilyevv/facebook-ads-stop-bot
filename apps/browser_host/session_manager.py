from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

from apps.browser_host.adapters.base import AntiDetectAdapter
from apps.browser_host.adapters.models import AdapterHealth, AutomationLaunchResult, OpenProfileInfo
from apps.browser_host.playwright_attach import AttachedBrowserSession, PlaywrightAttachService

_STOP_START_DELAY_SECONDS = 2.0


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

    async def ensure_profile_started(
        self,
        profile_id: str,
        launch_mode: str = "cdp",
        launch_args: list[str] | None = None,
    ) -> AutomationLaunchResult:
        logger = logging.getLogger(__name__)
        logger.info("Запрашиваю automation-сессию для профиля %s", profile_id)
        status = await self._adapter.get_profile_status(profile_id)
        if status.has_automation_binding:
            logger.info("Профиль %s уже готов к автоматизации", profile_id)
            existing_profile = await self._find_open_profile(profile_id)
            if existing_profile is not None and existing_profile.debug_endpoint:
                return self._build_existing_launch_result(profile_id, existing_profile)
            logger.warning(
                "Профиль %s помечен как готовый к автоматизации, но CDP endpoint не найден. "
                "Запускаю профиль повторно.",
                profile_id,
            )
        elif status.state == "RUNNING":
            logger.info("Профиль %s будет перезапущен с флагами автоматизации", profile_id)
            await self._adapter.stop_profile(profile_id)
            await asyncio.sleep(_STOP_START_DELAY_SECONDS)
        else:
            logger.info("Профиль %s остановлен, запускаю с автоматизацией", profile_id)

        return await self._adapter.start_profile_for_automation(
            profile_id=profile_id,
            launch_mode=launch_mode,
            launch_args=launch_args or [],
        )

    async def ensure_session(
        self,
        profile_id: str,
        launch_mode: str = "cdp",
        launch_args: list[str] | None = None,
    ) -> AttachedBrowserSession:
        launch_result = await self.ensure_profile_started(
            profile_id=profile_id,
            launch_mode=launch_mode,
            launch_args=launch_args,
        )
        return await self._playwright_attach_service.attach(launch_result)

    async def release_session(self, session: AttachedBrowserSession) -> None:
        """Освобождает временное Playwright-подключение после проверки или сканирования."""

        await self._playwright_attach_service.detach(session)

    async def _find_open_profile(self, profile_id: str) -> OpenProfileInfo | None:
        open_profiles = await self._adapter.list_open_profiles()
        for profile in open_profiles:
            if profile.profile_id == profile_id:
                return profile
        return None

    @staticmethod
    def _build_existing_launch_result(
        profile_id: str,
        profile: OpenProfileInfo,
    ) -> AutomationLaunchResult:
        debug_port = None
        if profile.debug_endpoint:
            parsed = urlparse(profile.debug_endpoint)
            debug_port = parsed.port
        return AutomationLaunchResult(
            profile_id=profile_id,
            vendor="vision",
            cdp_url=profile.debug_endpoint,
            webdriver_url=None,
            debug_port=debug_port,
            browser_pid=None,
            launched_at=datetime.now(tz=UTC),
        )
