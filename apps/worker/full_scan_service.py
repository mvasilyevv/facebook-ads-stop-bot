from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from apps.worker.pipeline_support import (
    build_initial_action_state,
    compute_priority_score,
    derive_risk_band,
    restore_last_action_from_history,
    restore_scope_from_existing_ad,
    serialize_scope_summary,
)
from core.domain import (
    ActionType,
    DecisionType,
    DeliveryStatus,
    RiskBand,
    ScanPipelineKind,
    ScanRunStatus,
    TelegramEventType,
    TrackingMode,
)
from core.repositories import (
    ActionJobsRepository,
    AdsRepository,
    BrowserRepository,
    DecisionsRepository,
    OffersRepository,
    ProfileLaunchesRepository,
    RulesRepository,
    ScanRunsRepository,
    SystemSettingsRepository,
    WatchlistRepository,
)
from core.repositories.notification_outbox import NotificationOutboxRepository
from core.rules.types import RulePercentages, RuleSwitches
from core.scanner import (
    ObserveScannerService,
    ScannedAdRow,
    ScannerPolicyFlags,
    ScannerScopeUnavailableError,
    build_scope_summary,
)
from core.scanner.protocols import ScannerProvider
from core.services import resolve_rule_runtime

_SETTING_AUTO_PAUSE_ENABLED = "auto_pause_enabled"
_SETTING_AUTO_RESUME_ENABLED = "auto_resume_enabled"
_SETTING_OBSERVE_ONLY_ENABLED = "observe_only_enabled"
_SOURCE_UNAVAILABLE_ERROR_MARKERS = (
    "Не удалось получить полный набор строк Ads Manager",
    "Не удалось получить полный scope из response Ads Manager",
)


@dataclass(slots=True, frozen=True)
class WorkerScanResult:
    """Краткий итог одного запуска полного сканирования."""

    scan_run_id: str | None
    rows_seen: int
    rows_parsed: int
    status: ScanRunStatus
    skip_reason: str | None = None


class FullScanService:
    """Собирает полный scope, считает риск и пишет watchlist/action jobs."""

    def __init__(
        self,
        *,
        async_session_factory,
        scanner_provider: ScannerProvider,
        pause_executor: Any | None = None,
        resume_executor: Any | None = None,
        notifier: Any | None = None,
        auto_pause_enabled: bool = False,
        auto_resume_enabled: bool = False,
        observe_only_enabled: bool = True,
        percentages: RulePercentages | None = None,
        recheck_interval_seconds: int = 15,
        suspend_after_consecutive_source_failures: int = 3,
    ) -> None:
        self._async_session_factory = async_session_factory
        self._scanner_provider = scanner_provider
        self._default_auto_pause_enabled = auto_pause_enabled
        self._default_auto_resume_enabled = auto_resume_enabled
        self._default_observe_only_enabled = observe_only_enabled
        self._default_percentages = percentages or RulePercentages()
        self._default_rule_switches = RuleSwitches()
        self._recheck_interval_seconds = max(int(recheck_interval_seconds), 5)
        self._suspend_after_consecutive_source_failures = max(
            int(suspend_after_consecutive_source_failures),
            1,
        )

    async def run_once(self, profile_id: str, browser_host_name: str) -> WorkerScanResult:
        async with self._async_session_factory() as session:
            browser_repo = BrowserRepository(session)
            profile_launches_repo = ProfileLaunchesRepository(session)
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
            profile_display_name = profile.display_name
            if profile.scan_suspended:
                skip_reason = profile.scan_suspend_reason or "Профиль находится на стопе"
                logging.getLogger(__name__).info(
                    "Полный скан профиля %s пропущен: профиль находится на стопе по причине `%s`",
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

            active_launch = await profile_launches_repo.ensure_active_profile_launch(profile_db_id)
            started_at = datetime.now(tz=UTC)

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
                    "Полный скан профиля %s пропущен: %s",
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

            scan_run = await scan_runs_repo.create_scan_run(
                browser_host_id=browser_host_id,
                profile_id=profile_db_id,
                profile_launch_id=active_launch.id,
                status=ScanRunStatus.RUNNING,
                started_at=started_at,
                pipeline_kind=ScanPipelineKind.FULL_SCAN,
                trigger_source="scheduler",
                target_fb_ad_ids=[],
            )

            try:
                collect_started_at = datetime.now(tz=UTC)
                scanned_rows = await self._collect_rows(
                    profile_id=profile_id,
                    browser_host_name=browser_host_name,
                )
                collect_ms = self._elapsed_ms(collect_started_at)

                persist_started_at = datetime.now(tz=UTC)
                persisted = await self._persist_rows(
                    session=session,
                    scan_run_id=scan_run.id,
                    profile_db_id=profile_db_id,
                    profile_id=profile_id,
                    browser_host_id=browser_host_id,
                    rows=scanned_rows,
                    auto_pause_enabled=auto_pause_enabled,
                    auto_resume_enabled=auto_resume_enabled,
                    observe_only_enabled=observe_only_enabled,
                    rule_percentages=rule_percentages,
                    rule_switches=rule_switches,
                )
                persist_ms = self._elapsed_ms(persist_started_at)

                ads_repo = AdsRepository(session)
                finished_at = datetime.now(tz=UTC)
                summary = build_scope_summary(scanned_rows, scanned_at=finished_at)
                await ads_repo.mark_unseen_ads(
                    seen_fb_ad_ids=[row.fb_ad_id for row in scanned_rows],
                    profile_id=profile_db_id,
                    profile_launch_id=active_launch.id,
                )
                await scan_runs_repo.update_scan_run(
                    scan_run.id,
                    status=ScanRunStatus.SUCCEEDED,
                    finished_at=finished_at,
                    rows_seen=summary.rows_seen,
                    rows_parsed=len(scanned_rows),
                    collect_ms=collect_ms,
                    evaluate_ms=persisted["evaluate_ms"],
                    persist_ms=persist_ms,
                    queue_ms=persisted["queue_ms"],
                    action_jobs_enqueued=persisted["action_jobs_enqueued"],
                    scope_summary=serialize_scope_summary(summary),
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
                await self._persist_failure(
                    profile_id=profile_id,
                    profile_db_id=profile_db_id,
                    profile_display_name=profile_display_name,
                    browser_host_id=browser_host_id,
                    browser_host_name=browser_host_name,
                    profile_launch_id=active_launch.id,
                    started_at=started_at,
                    error=exc,
                )
                raise

    async def _persist_rows(
        self,
        *,
        session,
        scan_run_id: str,
        profile_db_id: str,
        profile_id: str,
        browser_host_id: str,
        rows: list[ScannedAdRow],
        auto_pause_enabled: bool,
        auto_resume_enabled: bool,
        observe_only_enabled: bool,
        rule_percentages: RulePercentages,
        rule_switches: RuleSwitches,
    ) -> dict[str, int]:
        ads_repo = AdsRepository(session)
        offers_repo = OffersRepository(session)
        decisions_repo = DecisionsRepository(session)
        watchlist_repo = WatchlistRepository(session)
        action_jobs_repo = ActionJobsRepository(session)
        scanner = ObserveScannerService(
            percentages=rule_percentages,
            rule_switches=rule_switches,
        )

        evaluate_started_at = datetime.now(tz=UTC)
        known_ads = await ads_repo.get_ads_by_fb_ad_ids([row.fb_ad_id for row in rows])
        latest_successful_actions = await decisions_repo.get_latest_successful_actions(
            [row.fb_ad_id for row in rows]
        )
        clean_streaks = await decisions_repo.get_clean_streak_counts([row.fb_ad_id for row in rows])
        evaluate_ms = self._elapsed_ms(evaluate_started_at)

        queue_started_at = datetime.now(tz=UTC)
        action_jobs_enqueued = 0
        next_recheck_at = datetime.now(tz=UTC) + timedelta(seconds=self._recheck_interval_seconds)

        for raw_row in rows:
            existing_ad = known_ads.get(raw_row.fb_ad_id)
            row = restore_scope_from_existing_ad(row=raw_row, existing_ad=existing_ad)
            if existing_ad is not None:
                restore_last_action_from_history(
                    ad=existing_ad,
                    latest_successful_action=latest_successful_actions.get(row.fb_ad_id),
                )

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

            offer = await offers_repo.resolve_offer_for_ad(
                ad_name=row.ad_name,
                resolved_offer_code=row.resolved_offer_code,
                ad_id=row.fb_ad_id,
                adset_scope_key=row.adset_scope_key,
            )
            rate = None
            resolved_cpa_usd: Decimal | None = None
            if offer is not None:
                rate = await offers_repo.resolve_rate_version(
                    offer.id,
                    row.last_seen_at or datetime.now(tz=UTC),
                )
                if rate is not None:
                    resolved_cpa_usd = rate.cpa_usd

            prior_clean_streak = clean_streaks.get(row.fb_ad_id, 0)
            clean_streak = (
                prior_clean_streak + 1 if row.delivery_status == DeliveryStatus.PAUSED else 0
            )
            policy_flags = ScannerPolicyFlags(
                is_blocked=row.tracking_mode in {TrackingMode.MANUAL_BLOCK, TrackingMode.READ_ONLY},
                auto_resume_enabled=auto_resume_enabled,
                resume_owned_by_system=(
                    getattr(existing_ad, "last_action_source", None) == "автопауза"
                ),
            )
            decision_result = scanner.evaluate_row(
                row=row,
                resolved_cpa_usd=resolved_cpa_usd,
                policy_flags=policy_flags,
                clean_streak=clean_streak,
            )
            risk_band, risk_reason = derive_risk_band(
                row=row,
                decision_result=decision_result,
                rule_switches=rule_switches,
            )
            priority_score = compute_priority_score(
                row=row,
                decision_result=decision_result,
                risk_band=risk_band,
                rule_switches=rule_switches,
            )
            captured_at = row.last_seen_at or datetime.now(tz=UTC)

            ad = await ads_repo.upsert_ad(
                fb_ad_id=row.fb_ad_id,
                campaign_id=campaign.id,
                adset_id=adset.id,
                name=row.ad_name,
                delivery_status=row.delivery_status,
                tracking_mode=row.tracking_mode,
                scope_presence=row.scope_presence,
                last_seen_at=row.last_seen_at,
                last_action_source=getattr(existing_ad, "last_action_source", None),
                last_action_at=getattr(existing_ad, "last_action_at", None),
                last_decision=decision_result.decision,
                risk_band=risk_band,
                last_risk_reason=risk_reason,
                last_risk_at=captured_at if risk_band != RiskBand.SAFE else None,
                last_scan_run_id=scan_run_id,
            )
            await ads_repo.add_metric_snapshot(
                fb_ad_id=row.fb_ad_id,
                ad_id=ad.id,
                scan_run_id=scan_run_id,
                captured_at=captured_at,
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
                action_status=build_initial_action_state(
                    decision_result=decision_result,
                    auto_pause_enabled=auto_pause_enabled,
                    auto_resume_enabled=auto_resume_enabled,
                    observe_only_enabled=observe_only_enabled,
                ),
                created_at=captured_at,
            )

            if self._should_watch(row=row, risk_band=risk_band):
                await watchlist_repo.upsert_entry(
                    ad_id=ad.id,
                    fb_ad_id=row.fb_ad_id,
                    profile_id=profile_db_id,
                    browser_host_id=browser_host_id,
                    risk_band=risk_band,
                    priority_score=priority_score,
                    next_check_at=next_recheck_at,
                    last_reason=risk_reason,
                    last_metrics_at=captured_at,
                    source_scan_run_id=scan_run_id,
                )
            else:
                await watchlist_repo.delete_entry(row.fb_ad_id)

            action_type = self._resolve_action_type(
                decision=decision_result.decision,
                auto_pause_enabled=auto_pause_enabled,
                auto_resume_enabled=auto_resume_enabled,
                observe_only_enabled=observe_only_enabled,
            )
            if action_type is not None:
                await action_jobs_repo.enqueue_action_job(
                    decision_id=decision_record.id,
                    ad_id=ad.id,
                    fb_ad_id=row.fb_ad_id,
                    profile_id=profile_db_id,
                    browser_host_id=browser_host_id,
                    action_type=action_type,
                    priority_score=priority_score,
                    next_attempt_at=datetime.now(tz=UTC),
                )
                action_jobs_enqueued += 1

            if decision_result.decision == DecisionType.ALERT_REJECTION:
                await NotificationOutboxRepository(session).enqueue(
                    decision_id=decision_record.id,
                    event_type=TelegramEventType.AD_REJECTED_OR_NOT_DELIVERING,
                    payload_json={
                        "host": "worker",
                        "account_name": row.account_name or "unknown",
                        "campaign_name": row.campaign_name,
                        "adset_name": row.adset_name,
                        "ad_name": row.ad_name,
                        "fb_ad_id": row.fb_ad_id,
                        "reason": decision_result.reason,
                        "metrics": {},
                    },
                )

        queue_ms = self._elapsed_ms(queue_started_at)
        return {
            "evaluate_ms": evaluate_ms,
            "queue_ms": queue_ms,
            "action_jobs_enqueued": action_jobs_enqueued,
        }

    async def _collect_rows(self, *, profile_id: str, browser_host_name: str) -> list[ScannedAdRow]:
        return [
            self._coerce_scanned_row(item)
            for item in await self._scanner_provider.scan_rows(profile_id, browser_host_name)
        ]

    async def _persist_failure(
        self,
        *,
        profile_id: str,
        profile_db_id: str,
        profile_display_name: str,
        browser_host_id: str,
        browser_host_name: str,
        profile_launch_id: str,
        started_at: datetime,
        error: Exception,
    ) -> None:
        async with self._async_session_factory() as session:
            scan_runs_repo = ScanRunsRepository(session)
            browser_repo = BrowserRepository(session)
            outbox_repo = NotificationOutboxRepository(session)

            failed_scan_run = await scan_runs_repo.create_scan_run(
                browser_host_id=browser_host_id,
                profile_id=profile_db_id,
                profile_launch_id=profile_launch_id,
                status=ScanRunStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(tz=UTC),
                pipeline_kind=ScanPipelineKind.FULL_SCAN,
                trigger_source="scheduler",
                target_fb_ad_ids=[],
                error_message=str(error),
            )
            if isinstance(error, ScannerScopeUnavailableError):
                failure_streak = await self._count_consecutive_source_failures(
                    scan_runs_repo=scan_runs_repo,
                    profile_db_id=profile_db_id,
                )
                if failure_streak >= self._suspend_after_consecutive_source_failures:
                    await browser_repo.suspend_profile_scan(profile_id, str(error))
                    await outbox_repo.enqueue(
                        decision_id=None,
                        event_type=TelegramEventType.SCAN_SOURCE_UNAVAILABLE,
                        payload_json={
                            "host": browser_host_name,
                            "account_name": profile_display_name,
                            "campaign_name": "",
                            "adset_name": "",
                            "ad_name": "",
                            "fb_ad_id": "",
                            "reason": str(error),
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
        profile_db_id: str,
    ) -> int:
        recent_runs = await scan_runs_repo.list_recent_scan_runs_for_profile(
            profile_db_id,
            limit=max(self._suspend_after_consecutive_source_failures * 2, 10),
        )
        failure_streak = 0
        for run in recent_runs:
            if run.pipeline_kind != ScanPipelineKind.FULL_SCAN:
                continue
            if run.status != ScanRunStatus.FAILED:
                break
            if not self._is_source_unavailable_error(run.error_message):
                break
            failure_streak += 1
            if failure_streak >= self._suspend_after_consecutive_source_failures:
                break
        return failure_streak

    @staticmethod
    async def _resolve_bool_setting(
        *,
        settings_repo: SystemSettingsRepository,
        key: str,
        fallback: bool,
    ) -> bool:
        setting = await settings_repo.get_setting(key)
        if setting is None:
            return fallback
        return setting.value.lower() in ("true", "1", "yes", "on")

    @staticmethod
    def _should_watch(*, row: ScannedAdRow, risk_band: RiskBand) -> bool:
        return (
            risk_band in {RiskBand.WATCH, RiskBand.STOP}
            and row.delivery_status != DeliveryStatus.PAUSED
            and row.delivery_status != DeliveryStatus.NOT_DELIVERING
            and row.tracking_mode == TrackingMode.TRACKED
        )

    @staticmethod
    def _resolve_action_type(
        *,
        decision: DecisionType,
        auto_pause_enabled: bool,
        auto_resume_enabled: bool,
        observe_only_enabled: bool,
    ) -> ActionType | None:
        if observe_only_enabled:
            return None
        if decision == DecisionType.WOULD_PAUSE and auto_pause_enabled:
            return ActionType.PAUSE
        if decision == DecisionType.WOULD_RESUME and auto_resume_enabled:
            return ActionType.RESUME
        return None

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

    @staticmethod
    def _is_source_unavailable_error(message: str | None) -> bool:
        if not message:
            return False
        return any(marker in message for marker in _SOURCE_UNAVAILABLE_ERROR_MARKERS)

    @staticmethod
    def _elapsed_ms(started_at: datetime) -> int:
        return max(int((datetime.now(tz=UTC) - started_at).total_seconds() * 1000), 0)
