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
    RulesRepository,
)
from core.repositories.notification_outbox import NotificationOutboxRepository
from core.repositories.operations import ScanRunsRepository, SystemSettingsRepository
from core.rules.types import RulePercentages, RuleSwitches
from core.scanner import (
    ObserveScannerService,
    ScannedAdRow,
    ScannerDecisionResult,
    ScannerPolicyFlags,
    ScannerScopeUnavailableError,
    build_scope_summary,
)
from core.scanner.protocols import ScannerProvider
from core.services import resolve_rule_runtime

_SETTING_AUTO_PAUSE_ENABLED = "auto_pause_enabled"
_SETTING_AUTO_RESUME_ENABLED = "auto_resume_enabled"
_SETTING_OBSERVE_ONLY_ENABLED = "observe_only_enabled"
_EXECUTION_STATE_NOT_REQUIRED = "NOT_REQUIRED"
_EXECUTION_STATE_SKIPPED_BY_MODE = "SKIPPED_BY_MODE"
_EXECUTION_STATE_PENDING = "PENDING"
_SOURCE_UNAVAILABLE_ERROR_MARKERS = (
    "Не удалось получить полный набор строк Ads Manager",
    "Не удалось получить полный scope из response Ads Manager",
)
_AUTO_PAUSE_ACTION_SOURCE = "автопауза"
_AUTO_RESUME_ACTION_SOURCE = "авторезюм"


@dataclass(slots=True, frozen=True)
class WorkerScanResult:
    """Краткий итог одного запуска scanner worker."""

    scan_run_id: str | None
    rows_seen: int
    rows_parsed: int
    status: ScanRunStatus
    skip_reason: str | None = None


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
        suspend_after_consecutive_source_failures: int = 3,
    ) -> None:
        self._async_session_factory = async_session_factory
        self._scanner_provider = scanner_provider
        self._pause_executor = pause_executor
        self._resume_executor = resume_executor
        self._notifier = notifier
        self._default_auto_pause_enabled = auto_pause_enabled
        self._default_auto_resume_enabled = auto_resume_enabled
        self._default_observe_only_enabled = observe_only_enabled
        self._default_percentages = percentages or RulePercentages()
        self._default_rule_switches = RuleSwitches()
        self._suspend_after_consecutive_source_failures = max(
            int(suspend_after_consecutive_source_failures),
            1,
        )

    async def run_once(self, profile_id: str, browser_host_name: str) -> WorkerScanResult:
        async with self._async_session_factory() as session:
            browser_repo = BrowserRepository(session)
            scan_runs_repo = ScanRunsRepository(session)
            settings_repo = SystemSettingsRepository(session)

            browser_host = await browser_repo.get_browser_host_by_name(browser_host_name)
            if browser_host is None:
                raise RuntimeError(f"Browser host `{browser_host_name}` не найден в базе")
            browser_host_id = browser_host.id

            profile = await browser_repo.get_profile_by_vendor_id(profile_id)
            if profile is None:
                raise RuntimeError(f"Профиль `{profile_id}` не найден в базе")
            profile_db_id = profile.id
            if profile.scan_suspended:
                skip_reason = profile.scan_suspend_reason or "Профиль находится на стопе"
                logging.getLogger(__name__).info(
                    "Скан профиля %s пропущен: профиль находится на стопе по причине `%s`",
                    profile_id,
                    skip_reason,
                )
                return WorkerScanResult(
                    scan_run_id=None,
                    rows_seen=0,
                    rows_parsed=0,
                    status=ScanRunStatus.SKIPPED,
                    skip_reason=skip_reason,
                )

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
            rule_percentages, rule_switches = await self._resolve_rule_runtime(session)
            if not auto_pause_enabled and not auto_resume_enabled and not observe_only_enabled:
                skip_reason = "Автопауза, авторезюм и режим наблюдения выключены"
                logging.getLogger(__name__).info(
                    "Скан профиля %s пропущен: %s",
                    profile_id,
                    skip_reason,
                )
                return WorkerScanResult(
                    scan_run_id=None,
                    rows_seen=0,
                    rows_parsed=0,
                    status=ScanRunStatus.SKIPPED,
                    skip_reason=skip_reason,
                )

            started_at = datetime.now(tz=UTC)
            scan_run = await scan_runs_repo.create_scan_run(
                browser_host_id=browser_host_id,
                profile_id=profile_db_id,
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
                    rule_percentages=rule_percentages,
                    rule_switches=rule_switches,
                )
                ads_repo = AdsRepository(session)
                seen_fb_ad_ids = [row.fb_ad_id for row in scanned_rows]
                await ads_repo.mark_unseen_ads(
                    seen_fb_ad_ids=seen_fb_ad_ids,
                    profile_id=profile_db_id,
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
                    failed_scan_run = await scan_runs_repo.create_scan_run(
                        browser_host_id=browser_host_id,
                        profile_id=profile_db_id,
                        status=ScanRunStatus.FAILED,
                        started_at=started_at,
                        finished_at=datetime.now(tz=UTC),
                        error_message=str(exc),
                    )
                    if isinstance(exc, ScannerScopeUnavailableError):
                        failure_streak = await self._count_consecutive_source_failures(
                            scan_runs_repo=scan_runs_repo,
                            profile_db_id=profile_db_id,
                        )
                        if failure_streak >= self._suspend_after_consecutive_source_failures:
                            await browser_repo.suspend_profile_scan(profile_id, str(exc))
                            await NotificationOutboxRepository(session).enqueue(
                                decision_id=None,
                                event_type=TelegramEventType.SCAN_SOURCE_UNAVAILABLE,
                                payload_json={
                                    "host": browser_host_name,
                                    "account_name": profile.display_name,
                                    "campaign_name": "",
                                    "adset_name": "",
                                    "ad_name": "",
                                    "fb_ad_id": "",
                                    "reason": str(exc),
                                    "metrics": {},
                                    "extra": {
                                        "profile_id": profile_id,
                                        "attempts": str(failure_streak),
                                        "scan_run_id": str(failed_scan_run.id),
                                    },
                                },
                            )
                        else:
                            logging.getLogger(__name__).warning(
                                "Неполный scope Ads Manager для профиля %s: подряд %s ошибок из %s до автоматического стопа",
                                profile_id,
                                failure_streak,
                                self._suspend_after_consecutive_source_failures,
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
        rule_percentages: RulePercentages | None = None,
        rule_switches: RuleSwitches | None = None,
    ) -> None:
        ads_repo = AdsRepository(session)
        offers_repo = OffersRepository(session)
        decisions_repo = DecisionsRepository(session)
        scanner = ObserveScannerService(
            percentages=rule_percentages or self._default_percentages,
            rule_switches=rule_switches or self._default_rule_switches,
        )
        latest_successful_actions = await decisions_repo.get_latest_successful_actions(
            [row.fb_ad_id for row in rows]
        )

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
            self._restore_last_action_from_history(
                ad=ad,
                latest_successful_action=latest_successful_actions.get(row.fb_ad_id),
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
                resume_owned_by_system=ad.last_action_source == _AUTO_PAUSE_ACTION_SOURCE,
            )
            decision_result = scanner.evaluate_row(
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
                action_status=self._build_initial_execution_state(
                    decision_result=decision_result,
                    auto_pause_enabled=auto_pause_enabled,
                    auto_resume_enabled=auto_resume_enabled,
                    observe_only_enabled=observe_only_enabled,
                ),
                created_at=row.last_seen_at or datetime.now(tz=UTC),
            )
            ad.last_decision = decision_result.decision
            ad.last_scan_run_id = scan_run_id

            await self._maybe_execute_pause_action(
                ad=ad,
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
                ad=ad,
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

    async def _resolve_rule_runtime(self, session) -> tuple[RulePercentages, RuleSwitches]:
        repo = RulesRepository(session)
        try:
            runtime = await resolve_rule_runtime(repo)
            return runtime.percentages, runtime.switches
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "Не удалось разрешить runtime-правила из БД, использую значения по умолчанию"
            )
            return self._default_percentages, self._default_rule_switches

    async def _count_consecutive_source_failures(
        self,
        *,
        scan_runs_repo: ScanRunsRepository,
        profile_db_id,
    ) -> int:
        recent_runs = await scan_runs_repo.list_recent_scan_runs_for_profile(
            profile_db_id,
            limit=self._suspend_after_consecutive_source_failures,
        )
        failure_streak = 0
        for run in recent_runs:
            if run.status != ScanRunStatus.FAILED:
                break
            if not self._is_source_unavailable_error(run.error_message):
                break
            failure_streak += 1
        return failure_streak

    @staticmethod
    def _is_source_unavailable_error(message: str | None) -> bool:
        if not message:
            return False
        return any(marker in message for marker in _SOURCE_UNAVAILABLE_ERROR_MARKERS)

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

    @staticmethod
    def _build_initial_execution_state(
        *,
        decision_result: ScannerDecisionResult,
        auto_pause_enabled: bool,
        auto_resume_enabled: bool,
        observe_only_enabled: bool,
    ) -> str:
        if decision_result.decision not in {DecisionType.WOULD_PAUSE, DecisionType.WOULD_RESUME}:
            return _EXECUTION_STATE_NOT_REQUIRED
        if observe_only_enabled:
            return _EXECUTION_STATE_SKIPPED_BY_MODE
        if decision_result.decision == DecisionType.WOULD_PAUSE and not auto_pause_enabled:
            return _EXECUTION_STATE_SKIPPED_BY_MODE
        if decision_result.decision == DecisionType.WOULD_RESUME and not auto_resume_enabled:
            return _EXECUTION_STATE_SKIPPED_BY_MODE
        return _EXECUTION_STATE_PENDING

    async def _maybe_execute_pause_action(
        self,
        *,
        ad,
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
        await decisions_repo.set_decision_action_result(
            decision_id=decision.id,
            action_executed=False,
            action_status=_EXECUTION_STATE_PENDING,
        )

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
        if pause_result.success:
            ad.last_action_source = _AUTO_PAUSE_ACTION_SOURCE
            ad.last_action_at = datetime.now(tz=UTC)
        return pause_result

    async def _maybe_execute_resume_action(
        self,
        *,
        ad,
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
        await decisions_repo.set_decision_action_result(
            decision_id=decision.id,
            action_executed=False,
            action_status=_EXECUTION_STATE_PENDING,
        )

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
        if resume_result.success:
            ad.last_action_source = _AUTO_RESUME_ACTION_SOURCE
            ad.last_action_at = datetime.now(tz=UTC)
        return resume_result

    @staticmethod
    def _restore_last_action_from_history(
        *,
        ad,
        latest_successful_action: tuple[ActionType, datetime] | None,
    ) -> None:
        """Восстанавливает источник последнего действия из истории успешных action execution."""

        if latest_successful_action is None:
            return

        action_type, action_at = latest_successful_action
        restored_source = WorkerScanService._map_action_type_to_source(action_type)
        if restored_source is None:
            return
        if ad.last_action_at is not None and ad.last_action_at >= action_at:
            return

        ad.last_action_source = restored_source
        ad.last_action_at = action_at

    @staticmethod
    def _map_action_type_to_source(action_type: ActionType) -> str | None:
        """Преобразует тип action execution в человекочитаемый источник для карточки объявления."""

        if action_type == ActionType.PAUSE:
            return _AUTO_PAUSE_ACTION_SOURCE
        if action_type == ActionType.RESUME:
            return _AUTO_RESUME_ACTION_SOURCE
        return None

    @staticmethod
    def _map_decision_to_event_type(decision: DecisionType) -> TelegramEventType | None:
        """Маппит тип решения на тип Telegram-события."""

        mapping = {
            DecisionType.WOULD_PAUSE: TelegramEventType.OBSERVE_WOULD_PAUSE,
            DecisionType.WOULD_RESUME: TelegramEventType.OBSERVE_WOULD_RESUME,
            DecisionType.ALERT_REJECTION: TelegramEventType.AD_REJECTED_OR_NOT_DELIVERING,
        }
        return mapping.get(decision)
