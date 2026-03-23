from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
        self._session_lock = asyncio.Lock()
        self._leased_sessions: dict[str, tuple[AttachedBrowserSession, int]] = {}

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
        async with self._session_lock:
            cached_session = self._leased_sessions.get(profile_id)
            if cached_session is not None:
                session, lease_count = cached_session
                self._leased_sessions[profile_id] = (session, lease_count + 1)
                logging.getLogger(__name__).info(
                    "Переиспользую теплую Playwright-сессию для профиля %s, активных арендаторов: %s",
                    profile_id,
                    lease_count + 1,
                )
                return session

        launch_result = await self.ensure_profile_started(
            profile_id=profile_id,
            launch_mode=launch_mode,
            launch_args=launch_args,
        )
        session = await self._playwright_attach_service.attach(launch_result)

        async with self._session_lock:
            cached_session = self._leased_sessions.get(profile_id)
            if cached_session is not None:
                existing_session, lease_count = cached_session
                self._leased_sessions[profile_id] = (existing_session, lease_count + 1)
                with contextlib.suppress(Exception):
                    await self._playwright_attach_service.detach(session)
                logging.getLogger(__name__).info(
                    "Параллельная аренда Playwright-сессии для профиля %s уже существует, "
                    "использую ранее открытую сессию",
                    profile_id,
                )
                return existing_session

            self._leased_sessions[profile_id] = (session, 1)
            logging.getLogger(__name__).info(
                "Открыта теплая Playwright-сессия для профиля %s", profile_id
            )
            return session

    @asynccontextmanager
    async def lease_session(
        self,
        profile_id: str,
        launch_mode: str = "cdp",
        launch_args: list[str] | None = None,
    ) -> AsyncIterator[AttachedBrowserSession]:
        """Арендует Playwright-сессию на время батча действий."""

        session = await self.ensure_session(
            profile_id=profile_id,
            launch_mode=launch_mode,
            launch_args=launch_args,
        )
        try:
            yield session
        finally:
            await self.release_session(session)

    async def release_session(self, session: AttachedBrowserSession) -> None:
        """Освобождает временное Playwright-подключение после проверки или сканирования."""

        async with self._session_lock:
            cached_session = self._leased_sessions.get(session.profile_id)
            if cached_session is None:
                with contextlib.suppress(Exception):
                    await self._playwright_attach_service.detach(session)
                return

            cached_session_obj, lease_count = cached_session
            if cached_session_obj is not session:
                with contextlib.suppress(Exception):
                    await self._playwright_attach_service.detach(session)
                return

            next_lease_count = lease_count - 1
            if next_lease_count > 0:
                self._leased_sessions[session.profile_id] = (session, next_lease_count)
                logging.getLogger(__name__).info(
                    "Сохраняю теплую Playwright-сессию для профиля %s, активных арендаторов: %s",
                    session.profile_id,
                    next_lease_count,
                )
                return

            self._leased_sessions.pop(session.profile_id, None)

        await self._playwright_attach_service.detach(session)
        logging.getLogger(__name__).info(
            "Теплая Playwright-сессия для профиля %s освобождена", session.profile_id
        )

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
