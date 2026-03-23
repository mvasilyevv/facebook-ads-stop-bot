from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from apps.worker.pipeline_support import (
    build_initial_action_state,
    compute_priority_score,
    derive_risk_band,
    restore_last_action_from_history,
    restore_scope_from_existing_ad,
)
from core.domain import (
    ActionType,
    DecisionType,
    DeliveryStatus,
    RiskBand,
    ScanPipelineKind,
    ScanRunStatus,
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
from core.rules.types import RulePercentages, RuleSwitches
from core.scanner import ObserveScannerService, ScannedAdRow, ScannerPolicyFlags
from core.scanner.protocols import ScannerProvider
from core.services import resolve_rule_runtime

_SETTING_AUTO_PAUSE_ENABLED = "auto_pause_enabled"
_SETTING_OBSERVE_ONLY_ENABLED = "observe_only_enabled"


class TargetedRecheckService:
    """Подтверждает рискованные объявления свежими batch-метриками."""

    def __init__(
        self,
        *,
        async_session_factory,
        scanner_provider: ScannerProvider,
        auto_pause_enabled: bool = False,
        observe_only_enabled: bool = True,
        percentages: RulePercentages | None = None,
        recheck_interval_seconds: int = 15,
    ) -> None:
        self._async_session_factory = async_session_factory
        self._scanner_provider = scanner_provider
        self._default_auto_pause_enabled = auto_pause_enabled
        self._default_observe_only_enabled = observe_only_enabled
        self._default_percentages = percentages or RulePercentages()
        self._default_rule_switches = RuleSwitches()
        self._recheck_interval_seconds = max(int(recheck_interval_seconds), 5)

    async def run_once(self, *, limit: int = 50) -> int:
        async with self._async_session_factory() as session:
            entries = await WatchlistRepository(session).list_ready_entries(limit=limit)
            grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
            for entry in entries:
                if entry.profile_id is None or entry.browser_host_id is None:
                    continue
                grouped[(str(entry.profile_id), str(entry.browser_host_id))].append(entry)

        processed = 0
        for (profile_db_id, browser_host_id), group_entries in grouped.items():
            await self._process_group(
                profile_db_id=profile_db_id,
                browser_host_id=browser_host_id,
                entries=group_entries,
            )
            processed += len(group_entries)
        return processed

    async def _process_group(
        self,
        *,
        profile_db_id: str,
        browser_host_id: str,
        entries: list[Any],
    ) -> None:
        async with self._async_session_factory() as session:
            browser_repo = BrowserRepository(session)
            profile_launches_repo = ProfileLaunchesRepository(session)
            scan_runs_repo = ScanRunsRepository(session)
            settings_repo = SystemSettingsRepository(session)
            watchlist_repo = WatchlistRepository(session)

            profile = await browser_repo.get_profile(profile_db_id)
            browser_host = await browser_repo.get_browser_host(browser_host_id)
            if profile is None or browser_host is None:
                for entry in entries:
                    await watchlist_repo.delete_entry(entry.fb_ad_id)
                await session.commit()
                return

            active_launch = await profile_launches_repo.ensure_active_profile_launch(profile_db_id)
            started_at = datetime.now(tz=UTC)
            requested_fb_ad_ids = [entry.fb_ad_id for entry in entries]
            scan_run = await scan_runs_repo.create_scan_run(
                browser_host_id=browser_host_id,
                profile_id=profile_db_id,
                profile_launch_id=active_launch.id,
                status=ScanRunStatus.RUNNING,
                started_at=started_at,
                pipeline_kind=ScanPipelineKind.TARGETED_RECHECK,
                trigger_source="watchlist",
                target_fb_ad_ids=requested_fb_ad_ids,
            )

            auto_pause_enabled = await self._resolve_bool_setting(
                settings_repo=settings_repo,
                key=_SETTING_AUTO_PAUSE_ENABLED,
                fallback=self._default_auto_pause_enabled,
            )
            observe_only_enabled = await self._resolve_bool_setting(
                settings_repo=settings_repo,
                key=_SETTING_OBSERVE_ONLY_ENABLED,
                fallback=self._default_observe_only_enabled,
            )
            rule_percentages, rule_switches = await self._resolve_rule_runtime(session)

            collect_started_at = datetime.now(tz=UTC)
            recovery_result = await self._recover_rows(
                profile_id=profile.vendor_profile_id,
                browser_host_name=browser_host.name,
                requested_fb_ad_ids=requested_fb_ad_ids,
            )
            collect_ms = self._elapsed_ms(collect_started_at)

            if recovery_result["status"] != "complete":
                for entry in entries:
                    await watchlist_repo.schedule_retry(
                        entry.fb_ad_id,
                        next_check_at=datetime.now(tz=UTC)
                        + timedelta(seconds=self._build_retry_delay_seconds(entry.attempt_count)),
                        last_reason="Неполный targeted recheck, жду следующую попытку",
                    )
                await scan_runs_repo.update_scan_run(
                    scan_run.id,
                    status=ScanRunStatus.INVALID,
                    finished_at=datetime.now(tz=UTC),
                    rows_seen=len(recovery_result["rows"]),
                    rows_parsed=len(recovery_result["rows"]),
                    collect_ms=collect_ms,
                    error_message="Targeted recheck вернул неполное покрытие объявлений",
                    scope_summary={
                        "requested_fb_ad_ids": requested_fb_ad_ids,
                        "recovered_fb_ad_ids": recovery_result["recovered_fb_ad_ids"],
                        "missing_fb_ad_ids": recovery_result["missing_fb_ad_ids"],
                    },
                )
                await session.commit()
                return

            persist_started_at = datetime.now(tz=UTC)
            action_jobs_enqueued = await self._persist_rows(
                session=session,
                scan_run_id=scan_run.id,
                profile_db_id=profile_db_id,
                browser_host_id=browser_host_id,
                rows=recovery_result["rows"],
                auto_pause_enabled=auto_pause_enabled,
                observe_only_enabled=observe_only_enabled,
                rule_percentages=rule_percentages,
                rule_switches=rule_switches,
            )
            persist_ms = self._elapsed_ms(persist_started_at)
            await scan_runs_repo.update_scan_run(
                scan_run.id,
                status=ScanRunStatus.SUCCEEDED,
                finished_at=datetime.now(tz=UTC),
                rows_seen=len(recovery_result["rows"]),
                rows_parsed=len(recovery_result["rows"]),
                collect_ms=collect_ms,
                evaluate_ms=persist_ms,
                persist_ms=persist_ms,
                queue_ms=persist_ms,
                action_jobs_enqueued=action_jobs_enqueued,
                scope_summary={
                    "requested_fb_ad_ids": requested_fb_ad_ids,
                    "recovered_fb_ad_ids": recovery_result["recovered_fb_ad_ids"],
                    "missing_fb_ad_ids": recovery_result["missing_fb_ad_ids"],
                },
            )
            await session.commit()

    async def _persist_rows(
        self,
        *,
        session,
        scan_run_id: str,
        profile_db_id: str,
        browser_host_id: str,
        rows: list[ScannedAdRow],
        auto_pause_enabled: bool,
        observe_only_enabled: bool,
        rule_percentages: RulePercentages,
        rule_switches: RuleSwitches,
    ) -> int:
        ads_repo = AdsRepository(session)
        offers_repo = OffersRepository(session)
        decisions_repo = DecisionsRepository(session)
        watchlist_repo = WatchlistRepository(session)
        action_jobs_repo = ActionJobsRepository(session)
        scanner = ObserveScannerService(
            percentages=rule_percentages,
            rule_switches=rule_switches,
        )

        known_ads = await ads_repo.get_ads_by_fb_ad_ids([row.fb_ad_id for row in rows])
        latest_successful_actions = await decisions_repo.get_latest_successful_actions(
            [row.fb_ad_id for row in rows]
        )
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

            policy_flags = ScannerPolicyFlags(
                is_blocked=row.tracking_mode in {TrackingMode.MANUAL_BLOCK, TrackingMode.READ_ONLY},
                auto_resume_enabled=False,
                resume_owned_by_system=False,
            )
            decision_result = scanner.evaluate_row(
                row=row,
                resolved_cpa_usd=resolved_cpa_usd,
                policy_flags=policy_flags,
                clean_streak=0,
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
                    auto_resume_enabled=False,
                    observe_only_enabled=observe_only_enabled,
                ),
                created_at=captured_at,
            )

            if self._should_keep_on_watchlist(row=row, risk_band=risk_band):
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

            if (
                decision_result.decision == DecisionType.WOULD_PAUSE
                and auto_pause_enabled
                and not observe_only_enabled
                and row.delivery_status != DeliveryStatus.PAUSED
            ):
                await action_jobs_repo.enqueue_action_job(
                    decision_id=decision_record.id,
                    ad_id=ad.id,
                    fb_ad_id=row.fb_ad_id,
                    profile_id=profile_db_id,
                    browser_host_id=browser_host_id,
                    action_type=ActionType.PAUSE,
                    priority_score=priority_score,
                    next_attempt_at=datetime.now(tz=UTC),
                )
                action_jobs_enqueued += 1

        return action_jobs_enqueued

    async def _recover_rows(
        self,
        *,
        profile_id: str,
        browser_host_name: str,
        requested_fb_ad_ids: list[str],
    ) -> dict[str, Any]:
        recover_with_status = getattr(self._scanner_provider, "recover_rows_with_status", None)
        if callable(recover_with_status):
            result = await recover_with_status(profile_id, browser_host_name, requested_fb_ad_ids)
            return {
                "rows": [self._coerce_scanned_row(row) for row in getattr(result, "rows", [])],
                "status": getattr(result, "status", "inconclusive"),
                "recovered_fb_ad_ids": list(getattr(result, "recovered_fb_ad_ids", [])),
                "missing_fb_ad_ids": list(
                    getattr(result, "missing_fb_ad_ids", requested_fb_ad_ids)
                ),
            }

        recover_rows = self._scanner_provider.recover_rows  # type: ignore[attr-defined]
        recovered_rows = list(
            await recover_rows(
                profile_id,
                browser_host_name,
                requested_fb_ad_ids,
            )
        )
        recovered_fb_ad_ids = [row.fb_ad_id for row in recovered_rows]
        missing_fb_ad_ids = [
            fb_ad_id for fb_ad_id in requested_fb_ad_ids if fb_ad_id not in set(recovered_fb_ad_ids)
        ]
        return {
            "rows": [self._coerce_scanned_row(row) for row in recovered_rows],
            "status": "complete" if not missing_fb_ad_ids else "inconclusive",
            "recovered_fb_ad_ids": recovered_fb_ad_ids,
            "missing_fb_ad_ids": missing_fb_ad_ids,
        }

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
    def _should_keep_on_watchlist(*, row: ScannedAdRow, risk_band: RiskBand) -> bool:
        return (
            risk_band in {RiskBand.WATCH, RiskBand.STOP}
            and row.delivery_status != DeliveryStatus.PAUSED
            and row.delivery_status != DeliveryStatus.NOT_DELIVERING
            and row.tracking_mode == TrackingMode.TRACKED
        )

    def _build_retry_delay_seconds(self, attempt_count: int) -> int:
        base_delay = self._recheck_interval_seconds * max(attempt_count + 1, 2)
        return min(base_delay, 300)

    @staticmethod
    def _elapsed_ms(started_at: datetime) -> int:
        return max(int((datetime.now(tz=UTC) - started_at).total_seconds() * 1000), 0)

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
