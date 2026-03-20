from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.domain import DecisionType, DeliveryStatus, ScanRunStatus, TrackingMode
from core.repositories import (
    AdsRepository,
    BrowserRepository,
    DecisionsRepository,
    OffersRepository,
)
from core.repositories.operations import ScanRunsRepository
from core.rules.types import RulePercentages
from core.scanner import (
    ObserveScannerService,
    ScannedAdRow,
    ScannerPolicyFlags,
    build_scope_summary,
)


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
        scanner_provider: Any,
        notifier: Any | None = None,
        auto_resume_enabled: bool = False,
        percentages: RulePercentages | None = None,
    ) -> None:
        self._async_session_factory = async_session_factory
        self._scanner_provider = scanner_provider
        self._notifier = notifier
        self._auto_resume_enabled = auto_resume_enabled
        self._scanner = ObserveScannerService(percentages=percentages)

    async def run_once(self, profile_id: str, browser_host_name: str) -> WorkerScanResult:
        async with self._async_session_factory() as session:
            browser_repo = BrowserRepository(session)
            scan_runs_repo = ScanRunsRepository(session)
            browser_host = await browser_repo.get_browser_host_by_name(browser_host_name)
            if browser_host is None:
                raise RuntimeError(f"Browser host `{browser_host_name}` не найден в базе")

            profile = await browser_repo.get_profile_by_vendor_id(profile_id)
            if profile is None:
                raise RuntimeError(f"Профиль `{profile_id}` не найден в базе")

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
                    rows=scanned_rows,
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
                await scan_runs_repo.update_scan_run(
                    scan_run.id,
                    status=ScanRunStatus.FAILED,
                    finished_at=datetime.now(tz=UTC),
                    error_message=str(exc),
                )
                await session.commit()
                raise

    async def _persist_rows(
        self,
        *,
        session,
        scan_run_id,
        rows: list[ScannedAdRow],
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

            binding = await offers_repo.resolve_binding(row.fb_ad_id, row.adset_scope_key)
            rate = None
            resolved_cpa_usd = None
            if binding is not None:
                rate = await offers_repo.resolve_rate_version(
                    binding.offer_id,
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
                auto_resume_enabled=self._auto_resume_enabled,
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
                offer_id=binding.offer_id if binding is not None else None,
                offer_rate_version_id=rate.id if rate is not None else None,
                resolved_cpa_usd=resolved_cpa_usd,
            )
            await decisions_repo.create_decision(
                scan_run_id=scan_run_id,
                fb_ad_id=row.fb_ad_id,
                decision=decision_result.decision,
                reason=decision_result.reason,
                ad_id=ad.id,
                offer_id=binding.offer_id if binding is not None else None,
                offer_rate_version_id=rate.id if rate is not None else None,
                resolved_cpa_usd=resolved_cpa_usd,
                created_at=row.last_seen_at or datetime.now(tz=UTC),
            )
            ad.last_decision = decision_result.decision
            ad.last_scan_run_id = scan_run_id

            self._send_notification_if_supported(decision_result)

    async def _collect_rows(self, *, profile_id: str, browser_host_name: str) -> list[Any]:
        for method_name in ("scan_rows", "collect_rows", "scan", "collect", "run"):
            method = getattr(self._scanner_provider, method_name, None)
            if method is not None:
                return list(await method(profile_id, browser_host_name))

        if hasattr(self._scanner_provider, "__aiter__"):
            rows: list[Any] = []
            async for item in self._scanner_provider:
                rows.append(item)
            return rows

        raise RuntimeError("Scanner provider не поддерживает ожидаемый контракт чтения строк")

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
        decisions = await decisions_repo.list_decisions(fb_ad_id=fb_ad_id)
        streak = 0
        for item in decisions:
            if item.decision in {DecisionType.NO_ACTION, DecisionType.WOULD_RESUME}:
                streak += 1
                continue
            break
        return streak

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

    def _send_notification_if_supported(self, decision_result) -> None:
        # Уведомления будут подключены отдельным outbox-пайплайном после стабилизации scanner runtime.
        return None
