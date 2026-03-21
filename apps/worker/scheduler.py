from __future__ import annotations

import asyncio
import logging

from apps.browser_host.adapters.factory import build_adapter
from apps.browser_host.facebook_actions import FacebookAdsActionExecutor
from apps.browser_host.facebook_scanner import FacebookAdsScannerProvider
from apps.browser_host.playwright_attach import PlaywrightAttachService
from apps.browser_host.session_manager import BrowserSessionManager
from apps.worker.scan_service import WorkerScanService
from core.config import get_settings
from core.db import get_session_factory
from core.locks import ScanLockAcquisitionError, acquire_scan_lock
from core.redis import get_redis_client
from core.repositories import BrowserRepository


class SchedulerService:
    """Простой цикл scanner worker по активным профилям из базы."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._session_factory = get_session_factory()
        self._redis = get_redis_client()
        session_manager = BrowserSessionManager(
            adapter=build_adapter(self._settings),
            playwright_attach_service=PlaywrightAttachService(),
        )
        self._scan_service = WorkerScanService(
            async_session_factory=self._session_factory,
            scanner_provider=FacebookAdsScannerProvider(
                settings=self._settings,
                browser_session_manager=session_manager,
            ),
            pause_executor=FacebookAdsActionExecutor(session_manager=session_manager),
            auto_pause_enabled=self._settings.feature_auto_pause,
            auto_resume_enabled=self._settings.feature_auto_resume,
        )

    async def start(self) -> None:
        logger = logging.getLogger(__name__)
        logger.info(
            "Планировщик scanner worker запущен с интервалом %s секунд",
            self._settings.worker_scan_interval_seconds,
        )
        while True:
            await self._run_cycle()
            await asyncio.sleep(self._settings.worker_scan_interval_seconds)

    async def _run_cycle(self) -> None:
        logger = logging.getLogger(__name__)
        async with self._session_factory() as session:
            active_profiles = await BrowserRepository(session).list_active_profiles()

        if not active_profiles:
            logger.info("Активных профилей для сканирования пока нет")
            return

        for record in active_profiles:
            profile_id = record.profile.vendor_profile_id
            try:
                async with acquire_scan_lock(self._redis, profile_id):
                    result = await self._scan_service.run_once(
                        profile_id=profile_id,
                        browser_host_name=record.browser_host.name,
                    )
                    logger.info(
                        "Скан профиля %s завершен успешно: строк %s, run_id=%s",
                        profile_id,
                        result.rows_parsed,
                        result.scan_run_id,
                    )
            except ScanLockAcquisitionError:
                logger.info(
                    "Скан профиля %s пропущен — уже выполняется другим воркером",
                    profile_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Скан профиля %s завершился ошибкой: %s",
                    profile_id,
                    exc,
                )
