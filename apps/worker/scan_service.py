from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.actions import BrowserActionResult, PauseAdExecutor, ResumeAdExecutor
from core.domain import (
    ActionExecutionStatus,
    ActionType,
    DecisionType,
    DeliveryStatus,
    ScanRunStatus,
    TelegramEventType,
    TrackingMode,
)
from core.repositories import (
    AdsRepository,
    BrowserRepository,
    DecisionsRepository,
    OffersRepository,
)
from core.repositories.notification_outbox import NotificationOutboxRepository
from core.repositories.operations import ScanRunsRepository, SystemSettingsRepository
from core.rules.types import RulePercentages
from core.scanner import (
    ObserveScannerService,
    ScannedAdRow,
    ScannerDecisionResult,
    ScannerPolicyFlags,
    build_scope_summary,
)
from core.scanner.protocols import ScannerProvider

_SETTING_AUTO_PAUSE_ENABLED = "auto_pause_enabled"
_SETTING_AUTO_RESUME_ENABLED = "auto_resume_enabled"
_SETTING_OBSERVE_ONLY_ENABLED = "observe_only_enabled"


@dataclass(slots=True, frozen=True)
class WorkerScanResult:
    """Краткий итог одного запуска scanner worker."""

    scan_run_id: str
    rows_seen: int
    rows_parsed: int
    status: ScanRunStatus


class WorkerScanService:
    """Сохраняет результаты скана в БД и принимает observe-решения."""

    def __init__(
        self,
        *,
        async_session_factory,
        scanner_provider: ScannerProvider,
        pause_executor: PauseAdExecutor | None = None,
        resume_executor: ResumeAdExecutor | None = None,
        notifier: Any | None = None,
        auto_pause_enabled: bool = False,
        auto_resume_enabled: bool = False,
        observe_only_enabled: bool = True,
        percentages: RulePercentages | None = None,
    ) -> None:
        self._async_session_factory = async_session_factory
        self._scanner_provider = scanner_provider
        self._pause_executor = pause_executor
        self._resume_executor = resume_executor
        self._notifier = notifier
        self._default_auto_pause_enabled = auto_pause_enabled
        self._default_auto_resume_enabled = auto_resume_enabled
        self._default_observe_only_enabled = observe_only_enabled
        self._scanner = ObserveScannerService(percentages=percentages)

    async def run_once(self, profile_id: str, browser_host_name: str) -> WorkerScanResult:
        async with self._async_session_factory() as session:
            browser_repo = BrowserRepository(session)
            scan_runs_repo = ScanRunsRepository(session)
            settings_repo = SystemSettingsRepository(session)

            browser_host = await browser_repo.get_browser_host_by_name(browser_host_name)
            if browser_host is None:
                raise RuntimeError(f"Browser host `{browser_host_name}` не найден в базе")

            profile = await browser_repo.get_profile_by_vendor_id(profile_id)
            if profile is None:
                raise RuntimeError(f"Профиль `{profile_id}` не найден в базе")

            auto_pause_enabled = await self._resolve_bool_setting(
                settings_repo=settings_repo,
                key=_SETTING_AUTO_PAUSE_ENABLED,
                fallback=self._default_auto_pause_enabled,
            )
            auto_resume_enabled = await self._resolve_bool_setting(
                settings_repo=settings_repo,
                key=_SETTING_AUTO_RESUME_ENABLED,
                fallback=self._default_auto_resume_enabled,
            )
            observe_only_enabled = await self._resolve_bool_setting(
                settings_repo=settings_repo,
                key=_SETTING_OBSERVE_ONLY_ENABLED,
                fallback=self._default_observe_only_enabled,
            )

            started_at = datetime.now(tz=UTC)
            scan_run = await scan_runs_repo.create_scan_run(
                browser_host_id=browser_host.id,
                profile_id=profile.id,
                status=ScanRunStatus.RUNNING,
                started_at=started_at,
            )

            try:
                rows = await self._collect_rows(
                    profile_id=profile_id, browser_host_name=browser_host_name
                )
                scanned_rows = [self._coerce_scanned_row(item) for item in rows]
                await self._persist_rows(
                    session=session,
                    scan_run_id=scan_run.id,
                    profile_id=profile_id,
                    browser_host_name=browser_host_name,
                    rows=scanned_rows,
                    auto_pause_enabled=auto_pause_enabled,
                    auto_resume_enabled=auto_resume_enabled,
                    observe_only_enabled=observe_only_enabled,
                )
                ads_repo = AdsRepository(session)
                seen_fb_ad_ids = [row.fb_ad_id for row in scanned_rows]
                await ads_repo.mark_unseen_ads(
                    seen_fb_ad_ids=seen_fb_ad_ids,
                    profile_id=profile.id,
                )
                finished_at = datetime.now(tz=UTC)
                summary = build_scope_summary(scanned_rows, scanned_at=finished_at)
                await scan_runs_repo.update_scan_run(
                    scan_run.id,
                    status=ScanRunStatus.SUCCEEDED,
                    finished_at=finished_at,
                    rows_seen=summary.rows_seen,
                    rows_parsed=len(scanned_rows),
                    scope_summary=self._serialize_scope_summary(summary),
                )
                await session.commit()
                return WorkerScanResult(
                    scan_run_id=str(scan_run.id),
                    rows_seen=summary.rows_seen,
                    rows_parsed=len(scanned_rows),
                    status=ScanRunStatus.SUCCEEDED,
                )
            except Exception as exc:
                await session.rollback()
                try:
                    await scan_runs_repo.update_scan_run(
                        scan_run.id,
                        status=ScanRunStatus.FAILED,
                        finished_at=datetime.now(tz=UTC),
                        error_message=str(exc),
                    )
                    await session.commit()
                except Exception:  # noqa: BLE001
                    logging.getLogger(__name__).warning(
                        "Не удалось обновить статус scan_run после ошибки: %s",
                        exc,
                    )
                raise

    async def _persist_rows(
        self,
        *,
        session,
        scan_run_id,
        profile_id: str,
        browser_host_name: str,
        rows: list[ScannedAdRow],
        auto_pause_enabled: bool = False,
        auto_resume_enabled: bool = False,
        observe_only_enabled: bool = True,
    ) -> None:
        ads_repo = AdsRepository(session)
        offers_repo = OffersRepository(session)
        decisions_repo = DecisionsRepository(session)

        for row in rows:
            campaign = await ads_repo.upsert_campaign(
                scope_key=row.campaign_scope_key,
                name=row.campaign_name,
                tracking_mode=row.tracking_mode,
                last_seen_at=row.last_seen_at,
            )
            adset = await ads_repo.upsert_adset(
                scope_key=row.adset_scope_key,
                campaign_id=campaign.id,
                name=row.adset_name,
                tracking_mode=row.tracking_mode,
                last_seen_at=row.last_seen_at,
            )
            ad = await ads_repo.upsert_ad(
                fb_ad_id=row.fb_ad_id,
                campaign_id=campaign.id,
                adset_id=adset.id,
                name=row.ad_name,
                delivery_status=row.delivery_status,
                tracking_mode=row.tracking_mode,
                scope_presence=row.scope_presence,
                last_seen_at=row.last_seen_at,
                last_scan_run_id=scan_run_id,
            )

            offer = await offers_repo.resolve_offer_for_ad(
                ad_name=row.ad_name,
                resolved_offer_code=row.resolved_offer_code,
                ad_id=row.fb_ad_id,
                adset_scope_key=row.adset_scope_key,
            )
            rate = None
            resolved_cpa_usd = None
            if offer is not None:
                rate = await offers_repo.resolve_rate_version(
                    offer.id,
                    row.last_seen_at or datetime.now(tz=UTC),
                )
                if rate is not None:
                    resolved_cpa_usd = rate.cpa_usd

            prior_clean_streak = await self._get_prior_clean_streak(
                decisions_repo=decisions_repo,
                fb_ad_id=row.fb_ad_id,
            )
            clean_streak = (
                prior_clean_streak + 1 if row.delivery_status == DeliveryStatus.PAUSED else 0
            )
            policy_flags = ScannerPolicyFlags(
                is_blocked=row.tracking_mode in {TrackingMode.MANUAL_BLOCK, TrackingMode.READ_ONLY},
                auto_resume_enabled=auto_resume_enabled,
            )
            decision_result = self._scanner.evaluate_row(
                row=row,
                resolved_cpa_usd=resolved_cpa_usd,
                policy_flags=policy_flags,
                clean_streak=clean_streak,
            )

            await ads_repo.add_metric_snapshot(
                fb_ad_id=row.fb_ad_id,
                ad_id=ad.id,
                scan_run_id=scan_run_id,
                captured_at=row.last_seen_at or datetime.now(tz=UTC),
                spend=row.spend,
                clicks=row.clicks,
                cpc=row.cpc,
                leads=row.leads,
                cost_per_lead=row.cost_per_lead,
                registrations=row.registrations,
                cost_per_registration=row.cost_per_registration,
                deposits=row.deposits,
                offer_id=offer.id if offer is not None else None,
                offer_rate_version_id=rate.id if rate is not None else None,
                resolved_cpa_usd=resolved_cpa_usd,
            )
            decision_record = await decisions_repo.create_decision(
                scan_run_id=scan_run_id,
                fb_ad_id=row.fb_ad_id,
                decision=decision_result.decision,
                reason=decision_result.reason,
                ad_id=ad.id,
                offer_id=offer.id if offer is not None else None,
                offer_rate_version_id=rate.id if rate is not None else None,
                resolved_cpa_usd=resolved_cpa_usd,
                created_at=row.last_seen_at or datetime.now(tz=UTC),
            )
            ad.last_decision = decision_result.decision
            ad.last_scan_run_id = scan_run_id

            await self._maybe_execute_pause_action(
                decisions_repo=decisions_repo,
                decision=decision_record,
                decision_result=decision_result,
                profile_id=profile_id,
                browser_host_name=browser_host_name,
                fb_ad_id=row.fb_ad_id,
                auto_pause_enabled=auto_pause_enabled,
                observe_only_enabled=observe_only_enabled,
            )
            await self._maybe_execute_resume_action(
                decisions_repo=decisions_repo,
                decision=decision_record,
                decision_result=decision_result,
                profile_id=profile_id,
                browser_host_name=browser_host_name,
                fb_ad_id=row.fb_ad_id,
                auto_resume_enabled=auto_resume_enabled,
                observe_only_enabled=observe_only_enabled,
            )

            await self._enqueue_notification(
                outbox_repo=NotificationOutboxRepository(session),
                decision_id=decision_record.id,
                decision_result=decision_result,
                row=row,
            )

    async def _collect_rows(self, *, profile_id: str, browser_host_name: str) -> list[ScannedAdRow]:
        return list(await self._scanner_provider.scan_rows(profile_id, browser_host_name))

    @staticmethod
    async def _resolve_bool_setting(
        *,
        settings_repo: SystemSettingsRepository,
        key: str,
        fallback: bool,
    ) -> bool:
        """Возвращает логическую настройку из БД или значение по умолчанию."""

        setting = await settings_repo.get_setting(key)
        if setting is None:
            return fallback
        return setting.value.lower() in ("true", "1", "yes", "on")

    @staticmethod
    def _coerce_scanned_row(item: Any) -> ScannedAdRow:
        if isinstance(item, ScannedAdRow):
            return item

        last_seen_at = getattr(item, "last_seen_at", None) or getattr(item, "captured_at", None)
        return ScannedAdRow(
            fb_ad_id=item.fb_ad_id,
            campaign_scope_key=item.campaign_scope_key,
            adset_scope_key=item.adset_scope_key,
            campaign_name=item.campaign_name,
            adset_name=item.adset_name,
            ad_name=item.ad_name,
            delivery_status=item.delivery_status,
            tracking_mode=item.tracking_mode,
            scope_presence=item.scope_presence,
            spend=item.spend,
            clicks=item.clicks,
            cpc=item.cpc,
            leads=item.leads,
            cost_per_lead=item.cost_per_lead,
            registrations=item.registrations,
            cost_per_registration=item.cost_per_registration,
            deposits=item.deposits,
            last_seen_at=last_seen_at,
            account_name=getattr(item, "account_name", None),
            resolved_offer_id=getattr(item, "resolved_offer_id", None),
            resolved_offer_code=getattr(item, "resolved_offer_code", None),
        )

    async def _get_prior_clean_streak(
        self,
        *,
        decisions_repo: DecisionsRepository,
        fb_ad_id: str,
    ) -> int:
        return await decisions_repo.get_clean_streak_count(fb_ad_id)

    @staticmethod
    def _serialize_scope_summary(summary) -> dict[str, Any]:
        return {
            "rows_seen": summary.rows_seen,
            "rows_in_scope": summary.rows_in_scope,
            "rows_not_seen_this_scan": summary.rows_not_seen_this_scan,
            "rows_out_of_scope_confirmed": summary.rows_out_of_scope_confirmed,
            "active_rows": summary.active_rows,
            "paused_rows": summary.paused_rows,
            "not_delivering_rows": summary.not_delivering_rows,
            "manual_blocked_rows": summary.manual_blocked_rows,
            "read_only_rows": summary.read_only_rows,
            "unknown_rows": summary.unknown_rows,
            "scanned_at": summary.scanned_at.isoformat()
            if summary.scanned_at is not None
            else None,
            "fb_ad_ids": list(summary.fb_ad_ids),
        }

    async def _enqueue_notification(
        self,
        *,
        outbox_repo: NotificationOutboxRepository,
        decision_id: Any,
        decision_result: ScannerDecisionResult,
        row: ScannedAdRow,
    ) -> None:
        """Записывает уведомление в outbox-таблицу в рамках текущей транзакции."""

        event_type = self._map_decision_to_event_type(decision_result.decision)
        if event_type is None:
            return

        payload = {
            "host": "worker",
            "account_name": row.account_name or "unknown",
            "campaign_name": row.campaign_name,
            "adset_name": row.adset_name,
            "ad_name": row.ad_name,
            "fb_ad_id": row.fb_ad_id,
            "reason": decision_result.reason,
            "metrics": {
                "spend": str(row.spend),
                "clicks": row.clicks,
                "cpc": str(row.cpc) if row.cpc is not None else "n/a",
                "leads": row.leads,
                "cost_per_lead": str(row.cost_per_lead) if row.cost_per_lead is not None else "n/a",
                "registrations": row.registrations,
                "cost_per_registration": str(row.cost_per_registration)
                if row.cost_per_registration is not None
                else "n/a",
                "deposits": row.deposits,
            },
        }
        await outbox_repo.enqueue(
            decision_id=decision_id,
            event_type=event_type,
            payload_json=payload,
        )

    async def _maybe_execute_pause_action(
        self,
        *,
        decisions_repo: DecisionsRepository,
        decision,
        decision_result: ScannerDecisionResult,
        profile_id: str,
        browser_host_name: str,
        fb_ad_id: str,
        auto_pause_enabled: bool,
        observe_only_enabled: bool,
    ) -> BrowserActionResult | None:
        """При необходимости выполняет автопаузу и сохраняет результат в БД."""

        if not auto_pause_enabled:
            return None
        if observe_only_enabled:
            return None
        if decision_result.decision != DecisionType.WOULD_PAUSE:
            return None
        if self._pause_executor is None:
            return None

        logger = logging.getLogger(__name__)
        started_at = datetime.now(tz=UTC)

        try:
            pause_result = await self._pause_executor.pause_ad(
                profile_id=profile_id,
                browser_host_name=browser_host_name,
                fb_ad_id=fb_ad_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Не удалось выполнить автопаузу для объявления %s: %s",
                fb_ad_id,
                exc,
            )
            await decisions_repo.add_action_execution(
                decision_id=decision.id,
                action_type=ActionType.PAUSE,
                status=ActionExecutionStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(tz=UTC),
                message=str(exc),
            )
            await decisions_repo.set_decision_action_result(
                decision_id=decision.id,
                action_executed=False,
                action_status=ActionExecutionStatus.FAILED.value,
            )
            return BrowserActionResult(
                success=False,
                message=str(exc),
                fb_ad_id=fb_ad_id,
                profile_id=profile_id,
                browser_host_name=browser_host_name,
            )

        action_status = (
            ActionExecutionStatus.SUCCEEDED
            if pause_result.success
            else ActionExecutionStatus.FAILED
        )
        await decisions_repo.add_action_execution(
            decision_id=decision.id,
            action_type=ActionType.PAUSE,
            status=action_status,
            started_at=started_at,
            finished_at=datetime.now(tz=UTC),
            message=pause_result.message,
        )
        await decisions_repo.set_decision_action_result(
            decision_id=decision.id,
            action_executed=pause_result.success,
            action_status=action_status.value,
        )
        return pause_result

    async def _maybe_execute_resume_action(
        self,
        *,
        decisions_repo: DecisionsRepository,
        decision,
        decision_result: ScannerDecisionResult,
        profile_id: str,
        browser_host_name: str,
        fb_ad_id: str,
        auto_resume_enabled: bool,
        observe_only_enabled: bool,
    ) -> BrowserActionResult | None:
        """При необходимости выполняет автозапуск и сохраняет результат в БД."""

        if not auto_resume_enabled:
            return None
        if observe_only_enabled:
            return None
        if decision_result.decision != DecisionType.WOULD_RESUME:
            return None
        if self._resume_executor is None:
            return None

        logger = logging.getLogger(__name__)
        started_at = datetime.now(tz=UTC)

        try:
            resume_result = await self._resume_executor.resume_ad(
                profile_id=profile_id,
                browser_host_name=browser_host_name,
                fb_ad_id=fb_ad_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Не удалось выполнить авторезюм для объявления %s: %s",
                fb_ad_id,
                exc,
            )
            await decisions_repo.add_action_execution(
                decision_id=decision.id,
                action_type=ActionType.RESUME,
                status=ActionExecutionStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(tz=UTC),
                message=str(exc),
            )
            await decisions_repo.set_decision_action_result(
                decision_id=decision.id,
                action_executed=False,
                action_status=ActionExecutionStatus.FAILED.value,
            )
            return BrowserActionResult(
                success=False,
                message=str(exc),
                fb_ad_id=fb_ad_id,
                profile_id=profile_id,
                browser_host_name=browser_host_name,
            )

        action_status = (
            ActionExecutionStatus.SUCCEEDED
            if resume_result.success
            else ActionExecutionStatus.FAILED
        )
        await decisions_repo.add_action_execution(
            decision_id=decision.id,
            action_type=ActionType.RESUME,
            status=action_status,
            started_at=started_at,
            finished_at=datetime.now(tz=UTC),
            message=resume_result.message,
        )
        await decisions_repo.set_decision_action_result(
            decision_id=decision.id,
            action_executed=resume_result.success,
            action_status=action_status.value,
        )
        return resume_result

    @staticmethod
    def _map_decision_to_event_type(decision: DecisionType) -> TelegramEventType | None:
        """Маппит тип решения на тип Telegram-события."""

        mapping = {
            DecisionType.WOULD_PAUSE: TelegramEventType.OBSERVE_WOULD_PAUSE,
            DecisionType.WOULD_RESUME: TelegramEventType.OBSERVE_WOULD_RESUME,
            DecisionType.ALERT_REJECTION: TelegramEventType.AD_REJECTED_OR_NOT_DELIVERING,
        }
        return mapping.get(decision)
