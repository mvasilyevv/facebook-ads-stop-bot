from __future__ import annotations

import asyncio
import logging

from apps.browser_host.adapters.factory import build_adapter
from apps.browser_host.facebook_actions import FacebookAdsActionExecutor
from apps.browser_host.facebook_scanner import FacebookAdsScannerProvider
from apps.browser_host.playwright_attach import PlaywrightAttachService
from apps.browser_host.session_manager import BrowserSessionManager
from apps.notifier.formatter import TelegramMessageFormatter
from apps.notifier.http_transport import HttpTelegramTransport
from apps.notifier.outbox_processor import OutboxProcessor
from apps.notifier.sender import InMemoryDedupStore, TelegramSender
from apps.notifier.telegram import TelegramNotifier
from apps.worker.scan_service import WorkerScanService
from core.config import get_settings
from core.db import get_session_factory
from core.domain import ScanRunStatus
from core.locks import ScanLockAcquisitionError, acquire_scan_lock
from core.redis import get_redis_client
from core.repositories import BrowserRepository
from core.services import build_effective_settings, resolve_service_settings


class SchedulerService:
    """Простой цикл scanner worker по активным профилям из базы."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._session_factory = get_session_factory()
        self._redis = get_redis_client()

    async def start(self) -> None:
        logger = logging.getLogger(__name__)
        logger.info("Планировщик scanner worker запущен")
        while True:
            await self._run_cycle()
            interval = await self._resolve_scan_interval_seconds()
            logger.info("Следующий цикл сканирования начнется через %s секунд", interval)
            await asyncio.sleep(interval)

    def _build_outbox_processor(self, settings) -> OutboxProcessor:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            return OutboxProcessor(session_factory=self._session_factory, notifier=None)
        transport = HttpTelegramTransport(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
        sender = TelegramSender(
            transport=transport,
            dedup_store=InMemoryDedupStore(),
        )
        notifier = TelegramNotifier(
            formatter=TelegramMessageFormatter(),
            sender=sender,
        )
        return OutboxProcessor(session_factory=self._session_factory, notifier=notifier)

    def _build_scan_service(self, settings) -> WorkerScanService:
        session_manager = BrowserSessionManager(
            adapter=build_adapter(settings),
            playwright_attach_service=PlaywrightAttachService(),
        )
        action_executor = FacebookAdsActionExecutor(session_manager=session_manager)
        return WorkerScanService(
            async_session_factory=self._session_factory,
            scanner_provider=FacebookAdsScannerProvider(
                settings=settings,
                browser_session_manager=session_manager,
            ),
            pause_executor=action_executor,
            resume_executor=action_executor,
            auto_pause_enabled=settings.feature_auto_pause,
            auto_resume_enabled=settings.feature_auto_resume,
            observe_only_enabled=settings.feature_observe_only,
            suspend_after_consecutive_source_failures=(
                settings.scanner_suspend_after_consecutive_failures
            ),
        )

    async def _resolve_runtime_settings(self):
        async with self._session_factory() as session:
            runtime = await resolve_service_settings(session, base_settings=self._settings)
        return build_effective_settings(self._settings, runtime)

    async def _resolve_scan_interval_seconds(self) -> int:
        settings = await self._resolve_runtime_settings()
        return settings.worker_scan_interval_seconds

    async def _run_cycle(self) -> None:
        logger = logging.getLogger(__name__)
        settings = await self._resolve_runtime_settings()
        scan_service = self._build_scan_service(settings)
        async with self._session_factory() as session:
            active_profiles = await BrowserRepository(session).list_active_profiles()

        if not active_profiles:
            logger.info("Активных профилей для сканирования пока нет")
            return

        for record in active_profiles:
            profile_id = record.profile.vendor_profile_id
            try:
                async with acquire_scan_lock(self._redis, profile_id):
                    result = await scan_service.run_once(
                        profile_id=profile_id,
                        browser_host_name=record.browser_host.name,
                    )
                    result_status = getattr(result, "status", ScanRunStatus.SUCCEEDED)
                    if result_status == ScanRunStatus.SKIPPED:
                        skip_reason = getattr(result, "skip_reason", None)
                        if skip_reason:
                            logger.info(
                                "Скан профиля %s пропущен: %s",
                                profile_id,
                                skip_reason,
                            )
                        else:
                            logger.info("Скан профиля %s пропущен", profile_id)
                    elif result_status == ScanRunStatus.SUCCEEDED:
                        logger.info(
                            "Скан профиля %s завершен успешно: строк %s, run_id=%s",
                            profile_id,
                            result.rows_parsed,
                            result.scan_run_id,
                        )
                    else:
                        logger.info(
                            "Скан профиля %s завершен со статусом %s",
                            profile_id,
                            result_status,
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

        try:
            processed = await self._build_outbox_processor(settings).process_pending()
            if processed > 0:
                logger.info("Отправлено %s Telegram-уведомлений из outbox", processed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось обработать outbox-уведомления: %s", exc)
