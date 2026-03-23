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
from apps.worker.action_queue_service import ActionQueueService
from apps.worker.full_scan_service import FullScanService
from apps.worker.targeted_recheck_service import TargetedRecheckService
from core.config import get_settings
from core.db import get_session_factory
from core.domain import ScanRunStatus
from core.locks import ScanLockAcquisitionError, acquire_scan_lock
from core.redis import get_redis_client
from core.repositories import BrowserRepository
from core.services import build_effective_settings, resolve_service_settings

_ACTION_QUEUE_INTERVAL_SECONDS = 5


class SchedulerService:
    """Параллельные циклы полного скана, recheck и action queue."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._session_factory = get_session_factory()
        self._redis = get_redis_client()

    async def start(self) -> None:
        logger = logging.getLogger(__name__)
        logger.info("Планировщик fast-stop worker запущен")
        await asyncio.gather(
            self._run_full_scan_loop(),
            self._run_targeted_recheck_loop(),
            self._run_action_queue_loop(),
        )

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

    def _build_session_manager(self, settings) -> BrowserSessionManager:
        return BrowserSessionManager(
            adapter=build_adapter(settings),
            playwright_attach_service=PlaywrightAttachService(),
        )

    def _build_full_scan_service(self, settings) -> FullScanService:
        session_manager = self._build_session_manager(settings)
        return FullScanService(
            async_session_factory=self._session_factory,
            scanner_provider=FacebookAdsScannerProvider(
                settings=settings,
                browser_session_manager=session_manager,
            ),
            auto_pause_enabled=settings.feature_auto_pause,
            auto_resume_enabled=settings.feature_auto_resume,
            observe_only_enabled=settings.feature_observe_only,
            recheck_interval_seconds=settings.recheck_interval_seconds,
            suspend_after_consecutive_source_failures=(
                settings.scanner_suspend_after_consecutive_failures
            ),
        )

    def _build_targeted_recheck_service(self, settings) -> TargetedRecheckService:
        session_manager = self._build_session_manager(settings)
        return TargetedRecheckService(
            async_session_factory=self._session_factory,
            scanner_provider=FacebookAdsScannerProvider(
                settings=settings,
                browser_session_manager=session_manager,
            ),
            auto_pause_enabled=settings.feature_auto_pause,
            observe_only_enabled=settings.feature_observe_only,
            recheck_interval_seconds=settings.recheck_interval_seconds,
        )

    def _build_action_queue_service(self, settings) -> ActionQueueService:
        session_manager = self._build_session_manager(settings)
        action_executor = FacebookAdsActionExecutor(session_manager=session_manager)
        return ActionQueueService(
            async_session_factory=self._session_factory,
            pause_executor=action_executor,
            resume_executor=action_executor,
            profile_concurrency=settings.action_worker_concurrency,
        )

    async def _resolve_runtime_settings(self):
        async with self._session_factory() as session:
            runtime = await resolve_service_settings(session, base_settings=self._settings)
        return build_effective_settings(self._settings, runtime)

    async def _resolve_scan_interval_seconds(self) -> int:
        settings = await self._resolve_runtime_settings()
        return int(
            getattr(
                settings,
                "full_scan_interval_seconds",
                getattr(settings, "worker_scan_interval_seconds", 60),
            )
        )

    async def _run_cycle(self) -> None:
        logger = logging.getLogger(__name__)
        settings = await self._resolve_runtime_settings()
        build_scan_service = getattr(self, "_build_scan_service", None)
        if not callable(build_scan_service):
            build_scan_service = self._build_full_scan_service
        service = build_scan_service(settings)

        async with self._session_factory() as session:
            active_profiles = await BrowserRepository(session).list_active_profiles()

        if not active_profiles:
            logger.info("Активных профилей для полного сканирования пока нет")
            return

        for record in active_profiles:
            await self._run_full_scan_profile(
                service=service,
                semaphore=asyncio.Semaphore(1),
                profile_id=record.profile.vendor_profile_id,
                browser_host_name=record.browser_host.name,
            )

        await self._build_outbox_processor(settings).process_pending()

    async def _run_full_scan_loop(self) -> None:
        logger = logging.getLogger(__name__)
        while True:
            settings = await self._resolve_runtime_settings()
            service = self._build_full_scan_service(settings)
            async with self._session_factory() as session:
                active_profiles = await BrowserRepository(session).list_active_profiles()

            if not active_profiles:
                logger.info("Активных профилей для полного сканирования пока нет")
            else:
                semaphore = asyncio.Semaphore(max(settings.full_scan_profile_concurrency, 1))
                tasks = [
                    asyncio.create_task(
                        self._run_full_scan_profile(
                            service=service,
                            semaphore=semaphore,
                            profile_id=record.profile.vendor_profile_id,
                            browser_host_name=record.browser_host.name,
                        )
                    )
                    for record in active_profiles
                ]
                if tasks:
                    await asyncio.gather(*tasks)

            logger.info(
                "Следующий полный цикл сканирования начнется через %s секунд",
                settings.full_scan_interval_seconds,
            )
            await asyncio.sleep(settings.full_scan_interval_seconds)

    async def _run_targeted_recheck_loop(self) -> None:
        logger = logging.getLogger(__name__)
        while True:
            settings = await self._resolve_runtime_settings()
            service = self._build_targeted_recheck_service(settings)
            try:
                processed = await service.run_once(
                    limit=max(settings.action_worker_concurrency * 25, 50)
                )
                if processed > 0:
                    logger.info("Targeted recheck обработал %s watchlist-записей", processed)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Targeted recheck завершился ошибкой: %s", exc)

            await asyncio.sleep(settings.recheck_interval_seconds)

    async def _run_action_queue_loop(self) -> None:
        logger = logging.getLogger(__name__)
        while True:
            settings = await self._resolve_runtime_settings()
            service = self._build_action_queue_service(settings)
            try:
                processed = await service.run_once(
                    limit=max(settings.action_worker_concurrency * 25, 50)
                )
                if processed > 0:
                    logger.info("Очередь действий обработала %s job", processed)
                    await self._flush_outbox(settings)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Очередь действий завершилась ошибкой: %s", exc)

            await asyncio.sleep(_ACTION_QUEUE_INTERVAL_SECONDS)

    async def _run_full_scan_profile(
        self,
        *,
        service: FullScanService,
        semaphore: asyncio.Semaphore,
        profile_id: str,
        browser_host_name: str,
    ) -> None:
        logger = logging.getLogger(__name__)
        async with semaphore:
            try:
                async with acquire_scan_lock(self._redis, profile_id):
                    result = await service.run_once(
                        profile_id=profile_id,
                        browser_host_name=browser_host_name,
                    )
                    result_status = getattr(result, "status", ScanRunStatus.SUCCEEDED)
                    if result_status == ScanRunStatus.SKIPPED:
                        skip_reason = getattr(result, "skip_reason", None)
                        if skip_reason:
                            logger.info(
                                "Полный скан профиля %s пропущен: %s",
                                profile_id,
                                skip_reason,
                            )
                        else:
                            logger.info("Полный скан профиля %s пропущен", profile_id)
                    elif result_status == ScanRunStatus.SUCCEEDED:
                        logger.info(
                            "Полный скан профиля %s завершен успешно: строк %s, run_id=%s",
                            profile_id,
                            result.rows_parsed,
                            result.scan_run_id,
                        )
                    else:
                        logger.info(
                            "Полный скан профиля %s завершен со статусом %s",
                            profile_id,
                            result_status,
                        )
            except ScanLockAcquisitionError:
                logger.info(
                    "Полный скан профиля %s пропущен — уже выполняется другим воркером",
                    profile_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Полный скан профиля %s завершился ошибкой: %s",
                    profile_id,
                    exc,
                )

    async def _flush_outbox(self, settings) -> None:
        logger = logging.getLogger(__name__)
        try:
            processed = await self._build_outbox_processor(settings).process_pending()
            if processed > 0:
                logger.info("Отправлено %s Telegram-уведомлений из outbox", processed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось обработать outbox-уведомления: %s", exc)
