from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from apps.worker.action_queue_service import ActionQueueService
from apps.worker.scan_service import WorkerScanService
from apps.worker.targeted_recheck_service import TargetedRecheckService
from core.actions import BrowserActionResult
from core.domain import (
    ActionExecutionStatus,
    ActionJobStatus,
    ActionType,
    DecisionType,
    DeliveryStatus,
    EntityType,
    RiskBand,
    ScanPipelineKind,
    ScanRunStatus,
    ScopePresence,
    TelegramEventType,
    TrackingMode,
)
from core.models.advertising import MetricSnapshot
from core.models.operations import ActionExecution, ActionJob, TelegramEvent, WatchlistEntry
from core.repositories import (
    AdsRepository,
    BrowserRepository,
    DecisionsRepository,
    RulesRepository,
    ScanRunsRepository,
    SystemSettingsRepository,
    WatchlistRepository,
)
from core.scanner import ScannerScopeUnavailableError
from tests.fixtures.worker_scan_helpers import (
    FakeScannerProvider,
    WorkerScanRow,
    seed_offer_with_binding,
    seed_offer_with_rate,
    seed_worker_ad_graph,
)


async def _run_action_queue(
    async_session_factory,
    *,
    pause_executor=None,
    resume_executor=None,
) -> int:
    service = ActionQueueService(
        async_session_factory=async_session_factory,
        pause_executor=pause_executor,
        resume_executor=resume_executor,
        profile_concurrency=1,
    )
    return await service.run_once(limit=50)


class FakePauseExecutor:
    """Фейковый исполнитель автопаузы для проверки worker runtime."""

    def __init__(
        self,
        *,
        success: bool = True,
        message: str = "Объявление переведено на паузу",
    ) -> None:
        self.success = success
        self.message = message
        self.calls: list[tuple[str, str, str]] = []

    async def pause_ad(
        self,
        profile_id: str,
        browser_host_name: str,
        fb_ad_id: str,
    ) -> BrowserActionResult:
        self.calls.append((profile_id, browser_host_name, fb_ad_id))
        return BrowserActionResult(
            success=self.success,
            message=self.message,
            fb_ad_id=fb_ad_id,
            profile_id=profile_id,
            browser_host_name=browser_host_name,
        )


class FakeResumeExecutor:
    """Фейковый исполнитель авторезюма для проверки worker runtime."""

    def __init__(
        self,
        *,
        success: bool = True,
        message: str = "Объявление снова запущено",
    ) -> None:
        self.success = success
        self.message = message
        self.calls: list[tuple[str, str, str]] = []

    async def resume_ad(
        self,
        profile_id: str,
        browser_host_name: str,
        fb_ad_id: str,
    ) -> BrowserActionResult:
        self.calls.append((profile_id, browser_host_name, fb_ad_id))
        return BrowserActionResult(
            success=self.success,
            message=self.message,
            fb_ad_id=fb_ad_id,
            profile_id=profile_id,
            browser_host_name=browser_host_name,
        )


# Проверяет, что дорогой клик по объявлению с валидной CPA сохраняет снимок и пишет решение `WOULD_PAUSE`.
@pytest.mark.asyncio
async def test_worker_scan_service_writes_pause_decision_for_expensive_click(async_session_factory):
    seed = await seed_worker_ad_graph(async_session_factory)
    offer_id = await seed_offer_with_rate(
        async_session_factory,
        offer_code="offer-auto-worker-1",
        offer_name="DRC_CR2",
        cpa_usd=Decimal("5.00"),
        effective_from=datetime(2026, 3, 20, 11, 0, tzinfo=UTC),
    )
    provider = FakeScannerProvider(
        rows=[
            WorkerScanRow(
                campaign_scope_key=seed.campaign_scope_key,
                campaign_name=seed.campaign_name,
                adset_scope_key=seed.adset_scope_key,
                adset_name=seed.adset_name,
                fb_ad_id=seed.fb_ad_id,
                ad_name="DRC_CR2_CR001",
                delivery_status=DeliveryStatus.ACTIVE,
                tracking_mode=TrackingMode.TRACKED,
                scope_presence=ScopePresence.IN_SCOPE,
                spend=Decimal("0.38"),
                clicks=4,
                cpc=Decimal("0.11"),
                leads=0,
                cost_per_lead=None,
                registrations=0,
                cost_per_registration=None,
                deposits=0,
                captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
            )
        ]
    )
    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=provider,
        auto_pause_enabled=True,
    )

    await service.run_once(profile_id=seed.profile_id, browser_host_name=seed.browser_host_name)

    async with async_session_factory() as session:
        scan_runs = await ScanRunsRepository(session).list_scan_runs()
        decisions = await DecisionsRepository(session).list_decisions()
        snapshots = list(
            (
                await session.scalars(
                    select(MetricSnapshot).where(MetricSnapshot.fb_ad_id == seed.fb_ad_id)
                )
            ).all()
        )
        telegram_events = list((await session.scalars(select(TelegramEvent))).all())
        watchlist_entries = list(
            (
                await session.scalars(
                    select(WatchlistEntry).where(WatchlistEntry.fb_ad_id == seed.fb_ad_id)
                )
            ).all()
        )
        action_jobs = list(
            (
                await session.scalars(select(ActionJob).where(ActionJob.fb_ad_id == seed.fb_ad_id))
            ).all()
        )

    assert len(scan_runs) == 1
    assert scan_runs[0].status == ScanRunStatus.SUCCEEDED
    assert len(decisions) == 1
    assert decisions[0].decision == DecisionType.WOULD_PAUSE
    assert decisions[0].action_status == "SKIPPED_BY_MODE"
    assert decisions[0].resolved_cpa_usd == Decimal("5.00")
    assert str(decisions[0].offer_id) == str(offer_id)
    assert len(snapshots) == 1
    assert snapshots[0].fb_ad_id == seed.fb_ad_id
    assert snapshots[0].resolved_cpa_usd == Decimal("5.00")
    assert str(snapshots[0].offer_id) == str(offer_id)
    assert telegram_events == []
    assert len(watchlist_entries) == 1
    assert watchlist_entries[0].risk_band == RiskBand.STOP
    assert watchlist_entries[0].priority_score > 0
    assert action_jobs == []


# Проверяет, что объявление без resolved CPA сохраняет снимок и пишет решение `INSUFFICIENT_DATA`.
@pytest.mark.asyncio
async def test_worker_scan_service_marks_insufficient_data_without_cpa(async_session_factory):
    seed = await seed_worker_ad_graph(async_session_factory)
    provider = FakeScannerProvider(
        rows=[
            WorkerScanRow(
                campaign_scope_key=seed.campaign_scope_key,
                campaign_name=seed.campaign_name,
                adset_scope_key=seed.adset_scope_key,
                adset_name=seed.adset_name,
                fb_ad_id=seed.fb_ad_id,
                ad_name=seed.ad_name,
                delivery_status=DeliveryStatus.ACTIVE,
                tracking_mode=TrackingMode.TRACKED,
                scope_presence=ScopePresence.IN_SCOPE,
                spend=Decimal("0.14"),
                clicks=2,
                cpc=Decimal("0.07"),
                leads=0,
                cost_per_lead=None,
                registrations=0,
                cost_per_registration=None,
                deposits=0,
                captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
            )
        ]
    )
    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=provider,
        auto_pause_enabled=True,
    )

    await service.run_once(profile_id=seed.profile_id, browser_host_name=seed.browser_host_name)

    async with async_session_factory() as session:
        scan_runs = await ScanRunsRepository(session).list_scan_runs()
        decisions = await DecisionsRepository(session).list_decisions()
        snapshots = list(
            (
                await session.scalars(
                    select(MetricSnapshot).where(MetricSnapshot.fb_ad_id == seed.fb_ad_id)
                )
            ).all()
        )

    assert len(scan_runs) == 1
    assert scan_runs[0].status == ScanRunStatus.SUCCEEDED
    assert len(decisions) == 1
    assert decisions[0].decision == DecisionType.INSUFFICIENT_DATA
    assert decisions[0].resolved_cpa_usd is None
    assert len(snapshots) == 1
    assert snapshots[0].fb_ad_id == seed.fb_ad_id
    assert snapshots[0].resolved_cpa_usd is None


# Проверяет, что воркер берёт кастомные проценты из таблицы rules, а не из захардкоженных значений.
@pytest.mark.asyncio
async def test_worker_scan_service_uses_runtime_rule_multipliers(async_session_factory):
    seed = await seed_worker_ad_graph(async_session_factory)
    await seed_offer_with_rate(
        async_session_factory,
        offer_code="offer-auto-worker-runtime-rules",
        offer_name="DRC_CR2",
        cpa_usd=Decimal("5.00"),
        effective_from=datetime(2026, 3, 20, 11, 0, tzinfo=UTC),
    )
    async with async_session_factory() as session:
        repo = RulesRepository(session)
        await repo.ensure_default_rules()
        high_cpl_rule = await repo.get_rule_by_code("stop_high_cpl")
        assert high_cpl_rule is not None
        await repo.update_rule(
            str(high_cpl_rule.id),
            cpa_multiplier=Decimal("0.09"),
        )
        await session.commit()

    provider = FakeScannerProvider(
        rows=[
            WorkerScanRow(
                campaign_scope_key=seed.campaign_scope_key,
                campaign_name=seed.campaign_name,
                adset_scope_key=seed.adset_scope_key,
                adset_name=seed.adset_name,
                fb_ad_id=seed.fb_ad_id,
                ad_name="DRC_CR2_CR010",
                delivery_status=DeliveryStatus.ACTIVE,
                tracking_mode=TrackingMode.TRACKED,
                scope_presence=ScopePresence.IN_SCOPE,
                spend=Decimal("0.46"),
                clicks=8,
                cpc=Decimal("0.08"),
                leads=0,
                cost_per_lead=None,
                registrations=0,
                cost_per_registration=None,
                deposits=0,
                captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
            )
        ]
    )
    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=provider,
        auto_pause_enabled=True,
    )

    await service.run_once(profile_id=seed.profile_id, browser_host_name=seed.browser_host_name)

    async with async_session_factory() as session:
        decisions = await DecisionsRepository(session).list_decisions()

    assert len(decisions) == 1
    assert decisions[0].decision == DecisionType.WOULD_PAUSE
    assert decisions[0].reason == "Расход уже превысил порог лида без самого лида"


# Проверяет, что пустой успешный скан переводит объявления текущего профиля в `NOT_SEEN_THIS_SCAN`.
@pytest.mark.asyncio
async def test_worker_scan_service_marks_current_profile_ads_as_unseen_on_empty_scan(
    async_session_factory,
):
    seed = await seed_worker_ad_graph(async_session_factory)
    initial_provider = FakeScannerProvider(
        rows=[
            WorkerScanRow(
                campaign_scope_key=seed.campaign_scope_key,
                campaign_name=seed.campaign_name,
                adset_scope_key=seed.adset_scope_key,
                adset_name=seed.adset_name,
                fb_ad_id=seed.fb_ad_id,
                ad_name=seed.ad_name,
                delivery_status=DeliveryStatus.ACTIVE,
                tracking_mode=TrackingMode.TRACKED,
                scope_presence=ScopePresence.IN_SCOPE,
                spend=Decimal("0.10"),
                clicks=1,
                cpc=Decimal("0.10"),
                leads=0,
                cost_per_lead=None,
                registrations=0,
                cost_per_registration=None,
                deposits=0,
                captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
            )
        ]
    )
    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=initial_provider,
        auto_pause_enabled=True,
    )
    await service.run_once(profile_id=seed.profile_id, browser_host_name=seed.browser_host_name)

    empty_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(rows=[]),
        auto_pause_enabled=True,
    )
    await empty_service.run_once(
        profile_id=seed.profile_id, browser_host_name=seed.browser_host_name
    )

    async with async_session_factory() as session:
        ad = await AdsRepository(session).get_ad_by_fb_id(seed.fb_ad_id)

    assert ad is not None
    assert ad.scope_presence == ScopePresence.NOT_SEEN_THIS_SCAN


# Проверяет, что пустой скан одного профиля не портит `scope_presence` объявлений из другого профиля.
@pytest.mark.asyncio
async def test_worker_scan_service_marks_unseen_ads_only_inside_same_profile(async_session_factory):
    primary_seed = await seed_worker_ad_graph(async_session_factory)

    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        ads_repo = AdsRepository(session)

        browser_host = await browser_repo.upsert_browser_host(
            name="browser-host-secondary",
            vendor="vision",
            api_base_url="http://127.0.0.1:4040",
            is_enabled=True,
            last_heartbeat_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        profile = await browser_repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="vision-profile-2",
            display_name="Vision профиль 2",
            is_active=True,
            last_launch_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign-scope-2",
            name="Кампания 2",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset-scope-2",
            campaign_id=campaign.id,
            name="Адсет 2",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        secondary_ad = await ads_repo.upsert_ad(
            fb_ad_id="ad-2",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="Объявление 2",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        await session.commit()

    primary_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(
            rows=[
                WorkerScanRow(
                    campaign_scope_key=primary_seed.campaign_scope_key,
                    campaign_name=primary_seed.campaign_name,
                    adset_scope_key=primary_seed.adset_scope_key,
                    adset_name=primary_seed.adset_name,
                    fb_ad_id=primary_seed.fb_ad_id,
                    ad_name=primary_seed.ad_name,
                    delivery_status=DeliveryStatus.ACTIVE,
                    tracking_mode=TrackingMode.TRACKED,
                    scope_presence=ScopePresence.IN_SCOPE,
                    spend=Decimal("0.10"),
                    clicks=1,
                    cpc=Decimal("0.10"),
                    leads=0,
                    cost_per_lead=None,
                    registrations=0,
                    cost_per_registration=None,
                    deposits=0,
                    captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
                )
            ]
        ),
        auto_pause_enabled=True,
    )
    secondary_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(
            rows=[
                WorkerScanRow(
                    campaign_scope_key="campaign-scope-2",
                    campaign_name="Кампания 2",
                    adset_scope_key="adset-scope-2",
                    adset_name="Адсет 2",
                    fb_ad_id="ad-2",
                    ad_name="Объявление 2",
                    delivery_status=DeliveryStatus.ACTIVE,
                    tracking_mode=TrackingMode.TRACKED,
                    scope_presence=ScopePresence.IN_SCOPE,
                    spend=Decimal("0.10"),
                    clicks=1,
                    cpc=Decimal("0.10"),
                    leads=0,
                    cost_per_lead=None,
                    registrations=0,
                    cost_per_registration=None,
                    deposits=0,
                    captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
                )
            ]
        ),
        auto_pause_enabled=True,
    )

    await primary_service.run_once(
        profile_id=primary_seed.profile_id,
        browser_host_name=primary_seed.browser_host_name,
    )
    await secondary_service.run_once(
        profile_id=profile.vendor_profile_id,
        browser_host_name=browser_host.name,
    )

    empty_primary_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(rows=[]),
        auto_pause_enabled=True,
    )
    await empty_primary_service.run_once(
        profile_id=primary_seed.profile_id,
        browser_host_name=primary_seed.browser_host_name,
    )

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        primary_ad = await ads_repo.get_ad_by_fb_id(primary_seed.fb_ad_id)
        refreshed_secondary_ad = await ads_repo.get_ad_by_fb_id(secondary_ad.fb_ad_id)

    assert primary_ad is not None
    assert refreshed_secondary_ad is not None
    assert primary_ad.scope_presence == ScopePresence.NOT_SEEN_THIS_SCAN
    assert refreshed_secondary_ad.scope_presence == ScopePresence.IN_SCOPE


# Проверяет, что неполный второй скан помечает не увиденное объявление как `NOT_SEEN_THIS_SCAN`.
@pytest.mark.asyncio
async def test_worker_scan_service_marks_missing_ads_stale_after_partial_scan(
    async_session_factory,
):
    seed = await seed_worker_ad_graph(async_session_factory)

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign-scope-2",
            name="Кампания 2",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset-scope-2",
            campaign_id=campaign.id,
            name="Адсет 2",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        secondary_ad = await ads_repo.upsert_ad(
            fb_ad_id="ad-2",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="Объявление 2",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        await session.commit()

    full_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(
            rows=[
                WorkerScanRow(
                    campaign_scope_key=seed.campaign_scope_key,
                    campaign_name=seed.campaign_name,
                    adset_scope_key=seed.adset_scope_key,
                    adset_name=seed.adset_name,
                    fb_ad_id=seed.fb_ad_id,
                    ad_name=seed.ad_name,
                    delivery_status=DeliveryStatus.ACTIVE,
                    tracking_mode=TrackingMode.TRACKED,
                    scope_presence=ScopePresence.IN_SCOPE,
                    spend=Decimal("0.12"),
                    clicks=1,
                    cpc=Decimal("0.12"),
                    leads=0,
                    cost_per_lead=None,
                    registrations=0,
                    cost_per_registration=None,
                    deposits=0,
                    captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
                ),
                WorkerScanRow(
                    campaign_scope_key="campaign-scope-2",
                    campaign_name="Кампания 2",
                    adset_scope_key="adset-scope-2",
                    adset_name="Адсет 2",
                    fb_ad_id="ad-2",
                    ad_name="Объявление 2",
                    delivery_status=DeliveryStatus.PAUSED,
                    tracking_mode=TrackingMode.TRACKED,
                    scope_presence=ScopePresence.IN_SCOPE,
                    spend=Decimal("0.24"),
                    clicks=3,
                    cpc=Decimal("0.08"),
                    leads=1,
                    cost_per_lead=Decimal("0.24"),
                    registrations=0,
                    cost_per_registration=None,
                    deposits=0,
                    captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
                ),
            ]
        ),
        auto_pause_enabled=True,
    )
    partial_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(
            rows=[
                WorkerScanRow(
                    campaign_scope_key=seed.campaign_scope_key,
                    campaign_name=seed.campaign_name,
                    adset_scope_key=seed.adset_scope_key,
                    adset_name=seed.adset_name,
                    fb_ad_id=seed.fb_ad_id,
                    ad_name=seed.ad_name,
                    delivery_status=DeliveryStatus.ACTIVE,
                    tracking_mode=TrackingMode.TRACKED,
                    scope_presence=ScopePresence.IN_SCOPE,
                    spend=Decimal("0.15"),
                    clicks=2,
                    cpc=Decimal("0.08"),
                    leads=0,
                    cost_per_lead=None,
                    registrations=0,
                    cost_per_registration=None,
                    deposits=0,
                    captured_at=datetime(2026, 3, 20, 12, 5, tzinfo=UTC),
                )
            ]
        ),
        auto_pause_enabled=True,
    )

    await full_service.run_once(
        profile_id=seed.profile_id, browser_host_name=seed.browser_host_name
    )
    await partial_service.run_once(
        profile_id=seed.profile_id, browser_host_name=seed.browser_host_name
    )

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        primary_ad = await ads_repo.get_ad_by_fb_id(seed.fb_ad_id)
        refreshed_secondary_ad = await ads_repo.get_ad_by_fb_id(secondary_ad.fb_ad_id)
        scan_runs = await ScanRunsRepository(session).list_scan_runs()

    assert primary_ad is not None
    assert refreshed_secondary_ad is not None
    assert primary_ad.scope_presence == ScopePresence.IN_SCOPE
    assert refreshed_secondary_ad.scope_presence == ScopePresence.NOT_SEEN_THIS_SCAN
    assert scan_runs[0].status == ScanRunStatus.SUCCEEDED
    assert scan_runs[0].rows_seen == 1
    assert scan_runs[0].rows_parsed == 1


# Проверяет, что targeted recheck адресно добирает объявление из watchlist и создает pause-job по свежим метрикам.
@pytest.mark.asyncio
async def test_worker_scan_service_recovers_missing_ad_via_targeted_scope(async_session_factory):
    seed = await seed_worker_ad_graph(async_session_factory)
    await seed_offer_with_binding(
        async_session_factory,
        entity_type=EntityType.ADSET,
        entity_id="adset-scope-2",
        offer_code="offer-recheck",
        cpa_usd=Decimal("5.00"),
        effective_from=datetime(2026, 3, 20, 11, 0, tzinfo=UTC),
    )

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        browser_repo = BrowserRepository(session)
        profile = await browser_repo.get_profile_by_vendor_id(seed.profile_id)
        browser_host = await browser_repo.get_browser_host_by_name(seed.browser_host_name)
        assert profile is not None
        assert browser_host is not None
        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign-scope-2",
            name="Кампания 2",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset-scope-2",
            campaign_id=campaign.id,
            name="Адсет 2",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        secondary_ad = await ads_repo.upsert_ad(
            fb_ad_id="ad-2",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="Объявление 2",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        await WatchlistRepository(session).upsert_entry(
            ad_id=secondary_ad.id,
            fb_ad_id=secondary_ad.fb_ad_id,
            profile_id=profile.id,
            browser_host_id=browser_host.id,
            risk_band=RiskBand.STOP,
            priority_score=1200,
            next_check_at=datetime(2026, 3, 20, 12, 5, tzinfo=UTC),
            last_reason="Клик превысил допустимую долю CPA",
            last_metrics_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
            source_scan_run_id=None,
        )
        await session.commit()

    recovery_provider = FakeScannerProvider(
        rows=[],
        recovered_rows=[
            WorkerScanRow(
                campaign_scope_key="campaign-scope-2",
                campaign_name="Кампания 2",
                adset_scope_key="adset-scope-2",
                adset_name="Адсет 2",
                fb_ad_id="ad-2",
                ad_name="Объявление 2",
                delivery_status=DeliveryStatus.ACTIVE,
                tracking_mode=TrackingMode.TRACKED,
                scope_presence=ScopePresence.IN_SCOPE,
                spend=Decimal("0.38"),
                clicks=4,
                cpc=Decimal("0.11"),
                leads=0,
                cost_per_lead=None,
                registrations=0,
                cost_per_registration=None,
                deposits=0,
                captured_at=datetime(2026, 3, 20, 12, 5, tzinfo=UTC),
            )
        ],
    )
    recovery_service = TargetedRecheckService(
        async_session_factory=async_session_factory,
        scanner_provider=recovery_provider,
        auto_pause_enabled=True,
        observe_only_enabled=False,
    )

    processed = await recovery_service.run_once()

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        refreshed_secondary_ad = await ads_repo.get_ad_by_fb_id(secondary_ad.fb_ad_id)
        secondary_snapshots = list(
            (
                await session.scalars(
                    select(MetricSnapshot)
                    .where(MetricSnapshot.fb_ad_id == secondary_ad.fb_ad_id)
                    .order_by(MetricSnapshot.captured_at.desc(), MetricSnapshot.id.desc())
                )
            ).all()
        )
        action_jobs = list(
            (
                await session.scalars(
                    select(ActionJob).where(ActionJob.fb_ad_id == secondary_ad.fb_ad_id)
                )
            ).all()
        )
        watchlist_entries = list(
            (
                await session.scalars(
                    select(WatchlistEntry).where(WatchlistEntry.fb_ad_id == secondary_ad.fb_ad_id)
                )
            ).all()
        )
        scan_runs = await ScanRunsRepository(session).list_scan_runs()

    assert processed == 1
    assert recovery_provider.recover_calls == [(seed.profile_id, seed.browser_host_name, ("ad-2",))]
    assert refreshed_secondary_ad is not None
    assert refreshed_secondary_ad.scope_presence == ScopePresence.IN_SCOPE
    assert len(secondary_snapshots) == 1
    assert secondary_snapshots[0].spend == Decimal("0.38")
    assert secondary_snapshots[0].clicks == 4
    assert secondary_snapshots[0].cpc == Decimal("0.11")
    assert len(action_jobs) == 1
    assert action_jobs[0].action_type == ActionType.PAUSE
    assert action_jobs[0].status == ActionJobStatus.QUEUED
    assert len(watchlist_entries) == 1
    assert watchlist_entries[0].risk_band == RiskBand.STOP
    assert scan_runs[0].pipeline_kind == ScanPipelineKind.TARGETED_RECHECK
    assert scan_runs[0].rows_seen == 1
    assert scan_runs[0].rows_parsed == 1


# Проверяет, что в режиме наблюдения worker продолжает сканировать профиль даже при выключенных действиях.
@pytest.mark.asyncio
async def test_worker_scan_service_runs_scan_in_observe_mode_when_actions_disabled(
    async_session_factory,
):
    seed = await seed_worker_ad_graph(async_session_factory)
    provider = FakeScannerProvider(
        rows=[
            WorkerScanRow(
                campaign_scope_key=seed.campaign_scope_key,
                campaign_name=seed.campaign_name,
                adset_scope_key=seed.adset_scope_key,
                adset_name=seed.adset_name,
                fb_ad_id=seed.fb_ad_id,
                ad_name=seed.ad_name,
                delivery_status=DeliveryStatus.ACTIVE,
                tracking_mode=TrackingMode.TRACKED,
                scope_presence=ScopePresence.IN_SCOPE,
                spend=Decimal("0.05"),
                clicks=1,
                cpc=Decimal("0.05"),
                leads=0,
                cost_per_lead=None,
                registrations=0,
                cost_per_registration=None,
                deposits=0,
                captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
            )
        ]
    )
    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=provider,
        auto_pause_enabled=False,
        auto_resume_enabled=False,
        observe_only_enabled=True,
    )

    result = await service.run_once(
        profile_id=seed.profile_id,
        browser_host_name=seed.browser_host_name,
    )

    async with async_session_factory() as session:
        scan_runs = await ScanRunsRepository(session).list_scan_runs()

    assert provider.calls == [(seed.profile_id, seed.browser_host_name)]
    assert len(scan_runs) == 1
    assert result.status == ScanRunStatus.SUCCEEDED
    assert result.scan_run_id is not None
    assert result.rows_seen == 1
    assert result.rows_parsed == 1


# Проверяет, что worker пропускает профиль только когда выключены и действия, и режим наблюдения.
@pytest.mark.asyncio
async def test_worker_scan_service_skips_scan_when_all_modes_disabled(async_session_factory):
    seed = await seed_worker_ad_graph(async_session_factory)
    provider = FakeScannerProvider(rows=[])
    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=provider,
        auto_pause_enabled=False,
        auto_resume_enabled=False,
        observe_only_enabled=False,
    )

    result = await service.run_once(
        profile_id=seed.profile_id,
        browser_host_name=seed.browser_host_name,
    )

    async with async_session_factory() as session:
        scan_runs = await ScanRunsRepository(session).list_scan_runs()

    assert provider.calls == []
    assert scan_runs == []
    assert result.status == ScanRunStatus.SKIPPED
    assert result.skip_reason == "Автопауза, авторезюм и режим наблюдения выключены"
    assert result.scan_run_id is None
    assert result.rows_seen == 0
    assert result.rows_parsed == 0


# Проверяет, что автопауза вызывает executor и фиксирует выполненное действие в базе.
@pytest.mark.asyncio
async def test_worker_scan_service_executes_auto_pause_and_marks_decision(async_session_factory):
    seed = await seed_worker_ad_graph(async_session_factory)
    fake_executor = FakePauseExecutor(message="Объявление переведено на паузу")

    async with async_session_factory() as session:
        settings_repo = SystemSettingsRepository(session)
        await settings_repo.set_setting("auto_pause_enabled", "true")
        await settings_repo.set_setting("observe_only_enabled", "false")
        await session.commit()

    await seed_offer_with_rate(
        async_session_factory,
        offer_code="offer-auto-pause-1",
        offer_name="DRC_CR2",
        cpa_usd=Decimal("5.00"),
        effective_from=datetime(2026, 3, 20, 11, 0, tzinfo=UTC),
    )

    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(
            rows=[
                WorkerScanRow(
                    campaign_scope_key=seed.campaign_scope_key,
                    campaign_name=seed.campaign_name,
                    adset_scope_key=seed.adset_scope_key,
                    adset_name=seed.adset_name,
                    fb_ad_id=seed.fb_ad_id,
                    ad_name="DRC_CR2_CR002",
                    delivery_status=DeliveryStatus.ACTIVE,
                    tracking_mode=TrackingMode.TRACKED,
                    scope_presence=ScopePresence.IN_SCOPE,
                    spend=Decimal("0.38"),
                    clicks=4,
                    cpc=Decimal("0.11"),
                    leads=0,
                    cost_per_lead=None,
                    registrations=0,
                    cost_per_registration=None,
                    deposits=0,
                    captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
                )
            ]
        ),
        pause_executor=fake_executor,
        auto_pause_enabled=False,
        observe_only_enabled=True,
    )

    await service.run_once(profile_id=seed.profile_id, browser_host_name=seed.browser_host_name)
    processed_jobs = await _run_action_queue(
        async_session_factory,
        pause_executor=fake_executor,
    )

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        ad = await ads_repo.get_ad_by_fb_id(seed.fb_ad_id)
        assert ad is not None
        decisions_repo = DecisionsRepository(session)
        decisions = await decisions_repo.list_decisions(fb_ad_id=seed.fb_ad_id)
        jobs = list(
            (
                await session.scalars(select(ActionJob).where(ActionJob.fb_ad_id == seed.fb_ad_id))
            ).all()
        )
        action_executions = list(
            (
                await session.scalars(
                    select(ActionExecution).where(ActionExecution.decision_id == decisions[0].id)
                )
            ).all()
        )

    assert fake_executor.calls == [(seed.profile_id, seed.browser_host_name, seed.fb_ad_id)]
    assert processed_jobs == 1
    assert len(decisions) == 1
    assert decisions[0].decision == DecisionType.WOULD_PAUSE
    assert decisions[0].action_executed is True
    assert decisions[0].action_status == ActionExecutionStatus.SUCCEEDED.value
    assert len(jobs) == 1
    assert jobs[0].status == ActionJobStatus.SUCCEEDED
    assert len(action_executions) == 1
    assert action_executions[0].action_type == ActionType.PAUSE
    assert action_executions[0].status == ActionExecutionStatus.SUCCEEDED
    assert action_executions[0].message == "Объявление переведено на паузу"
    assert ad.last_action_source == "автопауза"
    assert ad.last_action_at is not None


# Проверяет, что в режиме наблюдения автопауза не выполняется физически даже при включенном праве на паузу.
@pytest.mark.asyncio
async def test_worker_scan_service_keeps_pause_in_observe_mode(async_session_factory):
    seed = await seed_worker_ad_graph(async_session_factory)
    fake_executor = FakePauseExecutor(message="Объявление переведено на паузу")

    async with async_session_factory() as session:
        settings_repo = SystemSettingsRepository(session)
        await settings_repo.set_setting("auto_pause_enabled", "true")
        await settings_repo.set_setting("observe_only_enabled", "true")
        await session.commit()

    await seed_offer_with_binding(
        async_session_factory,
        entity_type=EntityType.ADSET,
        entity_id=seed.adset_scope_key,
        offer_code="offer-observe",
        cpa_usd=Decimal("5.00"),
        effective_from=datetime(2026, 3, 20, 11, 0, tzinfo=UTC),
    )

    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(
            rows=[
                WorkerScanRow(
                    campaign_scope_key=seed.campaign_scope_key,
                    campaign_name=seed.campaign_name,
                    adset_scope_key=seed.adset_scope_key,
                    adset_name=seed.adset_name,
                    fb_ad_id=seed.fb_ad_id,
                    ad_name=seed.ad_name,
                    delivery_status=DeliveryStatus.ACTIVE,
                    tracking_mode=TrackingMode.TRACKED,
                    scope_presence=ScopePresence.IN_SCOPE,
                    spend=Decimal("0.38"),
                    clicks=4,
                    cpc=Decimal("0.11"),
                    leads=0,
                    cost_per_lead=None,
                    registrations=0,
                    cost_per_registration=None,
                    deposits=0,
                    captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
                )
            ]
        ),
        pause_executor=fake_executor,
        auto_pause_enabled=False,
        observe_only_enabled=False,
    )

    await service.run_once(profile_id=seed.profile_id, browser_host_name=seed.browser_host_name)

    async with async_session_factory() as session:
        decisions_repo = DecisionsRepository(session)
        decisions = await decisions_repo.list_decisions(fb_ad_id=seed.fb_ad_id)
        action_executions = list(
            (
                await session.scalars(
                    select(ActionExecution).where(ActionExecution.decision_id == decisions[0].id)
                )
            ).all()
        )

    assert fake_executor.calls == []
    assert len(decisions) == 1
    assert decisions[0].decision == DecisionType.WOULD_PAUSE
    assert decisions[0].action_executed is False
    assert decisions[0].action_status == "SKIPPED_BY_MODE"
    assert action_executions == []


class _AlwaysFailingScannerProvider:
    """Фейковый scanner provider, который триггерит стоп профиля после деградации source."""

    async def scan_rows(self, profile_id: str, browser_host_name: str):
        raise ScannerScopeUnavailableError(
            "Не удалось получить полный scope из response Ads Manager"
        )


# Проверяет, что уже остановленный профиль полностью пропускается worker и не запускает scanner.
@pytest.mark.asyncio
async def test_worker_scan_service_skips_already_suspended_profile(async_session_factory):
    seed = await seed_worker_ad_graph(async_session_factory)

    async with async_session_factory() as session:
        await BrowserRepository(session).suspend_profile_scan(
            seed.profile_id,
            "Ручной стоп перед запуском worker",
        )
        await session.commit()

    provider = FakeScannerProvider(
        rows=[
            WorkerScanRow(
                campaign_scope_key=seed.campaign_scope_key,
                campaign_name=seed.campaign_name,
                adset_scope_key=seed.adset_scope_key,
                adset_name=seed.adset_name,
                fb_ad_id=seed.fb_ad_id,
                ad_name=seed.ad_name,
                delivery_status=DeliveryStatus.ACTIVE,
                tracking_mode=TrackingMode.TRACKED,
                scope_presence=ScopePresence.IN_SCOPE,
                spend=Decimal("0.10"),
                clicks=1,
                cpc=Decimal("0.10"),
                leads=0,
                cost_per_lead=None,
                registrations=0,
                cost_per_registration=None,
                deposits=0,
                captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
            )
        ]
    )
    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=provider,
        auto_pause_enabled=True,
    )

    result = await service.run_once(
        profile_id=seed.profile_id,
        browser_host_name=seed.browser_host_name,
    )

    async with async_session_factory() as session:
        scan_runs = await ScanRunsRepository(session).list_scan_runs()
        profile = await BrowserRepository(session).get_profile_by_vendor_id(seed.profile_id)

    assert provider.calls == []
    assert scan_runs == []
    assert profile is not None
    assert profile.scan_suspended is True
    assert result.status == ScanRunStatus.SKIPPED
    assert result.scan_run_id is None


# Проверяет, что деградация scanner source ставит профиль на стоп только после нескольких подряд сбоев и пишет alert в outbox.
@pytest.mark.asyncio
async def test_worker_scan_service_suspends_profile_when_scanner_source_is_unavailable(
    async_session_factory,
):
    seed = await seed_worker_ad_graph(async_session_factory)
    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=_AlwaysFailingScannerProvider(),
        auto_pause_enabled=True,
    )

    for _ in range(2):
        with pytest.raises(
            ScannerScopeUnavailableError,
            match="Не удалось получить полный scope из response Ads Manager",
        ):
            await service.run_once(
                profile_id=seed.profile_id,
                browser_host_name=seed.browser_host_name,
            )

    async with async_session_factory() as session:
        profile_after_two_failures = await BrowserRepository(session).get_profile_by_vendor_id(
            seed.profile_id
        )
        scan_runs_after_two_failures = await ScanRunsRepository(session).list_scan_runs()
        telegram_events_after_two_failures = list(
            (await session.scalars(select(TelegramEvent))).all()
        )

    assert profile_after_two_failures is not None
    assert profile_after_two_failures.scan_suspended is False
    assert len(scan_runs_after_two_failures) == 2
    assert all(run.status == ScanRunStatus.FAILED for run in scan_runs_after_two_failures)
    assert telegram_events_after_two_failures == []

    with pytest.raises(
        ScannerScopeUnavailableError,
        match="Не удалось получить полный scope из response Ads Manager",
    ):
        await service.run_once(profile_id=seed.profile_id, browser_host_name=seed.browser_host_name)

    async with async_session_factory() as session:
        profile = await BrowserRepository(session).get_profile_by_vendor_id(seed.profile_id)
        scan_runs = await ScanRunsRepository(session).list_scan_runs()
        telegram_events = list((await session.scalars(select(TelegramEvent))).all())

    assert profile is not None
    assert profile.scan_suspended is True
    assert profile.scan_suspend_reason == "Не удалось получить полный scope из response Ads Manager"
    assert len(scan_runs) == 3
    assert all(run.status == ScanRunStatus.FAILED for run in scan_runs)
    assert len(telegram_events) == 1
    assert telegram_events[0].event_type == TelegramEventType.SCAN_SOURCE_UNAVAILABLE


# Проверяет, что авторезюм вызывает executor и пишет в БД выполненное действие после двух чистых сканов.
@pytest.mark.asyncio
async def test_worker_scan_service_executes_auto_resume_and_marks_decision(async_session_factory):
    seed = await seed_worker_ad_graph(async_session_factory)
    fake_executor = FakeResumeExecutor(message="Объявление снова запущено")

    async with async_session_factory() as session:
        settings_repo = SystemSettingsRepository(session)
        await settings_repo.set_setting("auto_resume_enabled", "true")
        await settings_repo.set_setting("observe_only_enabled", "false")
        await session.commit()

    await seed_offer_with_binding(
        async_session_factory,
        entity_type=EntityType.ADSET,
        entity_id=seed.adset_scope_key,
        offer_code="offer-resume",
        cpa_usd=Decimal("5.00"),
        effective_from=datetime(2026, 3, 20, 11, 0, tzinfo=UTC),
    )

    warmup_row_first = WorkerScanRow(
        campaign_scope_key=seed.campaign_scope_key,
        campaign_name=seed.campaign_name,
        adset_scope_key=seed.adset_scope_key,
        adset_name=seed.adset_name,
        fb_ad_id=seed.fb_ad_id,
        ad_name=seed.ad_name,
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.20"),
        clicks=2,
        cpc=Decimal("0.05"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=1,
        cost_per_registration=Decimal("0.20"),
        deposits=0,
        captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
    )
    warmup_row_second = WorkerScanRow(
        campaign_scope_key=seed.campaign_scope_key,
        campaign_name=seed.campaign_name,
        adset_scope_key=seed.adset_scope_key,
        adset_name=seed.adset_name,
        fb_ad_id=seed.fb_ad_id,
        ad_name=seed.ad_name,
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.20"),
        clicks=2,
        cpc=Decimal("0.05"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=1,
        cost_per_registration=Decimal("0.20"),
        deposits=0,
        captured_at=datetime(2026, 3, 20, 12, 1, tzinfo=UTC),
    )
    resume_row = WorkerScanRow(
        campaign_scope_key=seed.campaign_scope_key,
        campaign_name=seed.campaign_name,
        adset_scope_key=seed.adset_scope_key,
        adset_name=seed.adset_name,
        fb_ad_id=seed.fb_ad_id,
        ad_name=seed.ad_name,
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.20"),
        clicks=2,
        cpc=Decimal("0.05"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=1,
        cost_per_registration=Decimal("0.20"),
        deposits=0,
        captured_at=datetime(2026, 3, 20, 12, 2, tzinfo=UTC),
    )

    warmup_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(rows=[warmup_row_first]),
    )
    await warmup_service.run_once(
        profile_id=seed.profile_id, browser_host_name=seed.browser_host_name
    )
    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        ad = await ads_repo.get_ad_by_fb_id(seed.fb_ad_id)
        assert ad is not None
        ad.last_action_source = "автопауза"
        await session.commit()

    warmup_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(rows=[warmup_row_second]),
    )
    await warmup_service.run_once(
        profile_id=seed.profile_id, browser_host_name=seed.browser_host_name
    )

    resume_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(rows=[resume_row]),
        resume_executor=fake_executor,
        auto_resume_enabled=False,
        observe_only_enabled=True,
    )

    await resume_service.run_once(
        profile_id=seed.profile_id, browser_host_name=seed.browser_host_name
    )
    processed_jobs = await _run_action_queue(
        async_session_factory,
        resume_executor=fake_executor,
    )

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        ad = await ads_repo.get_ad_by_fb_id(seed.fb_ad_id)
        assert ad is not None
        decisions_repo = DecisionsRepository(session)
        decisions = await decisions_repo.list_decisions(fb_ad_id=seed.fb_ad_id)
        latest_decision = decisions[0]
        jobs = list(
            (
                await session.scalars(select(ActionJob).where(ActionJob.fb_ad_id == seed.fb_ad_id))
            ).all()
        )
        action_executions = list(
            (
                await session.scalars(
                    select(ActionExecution).where(ActionExecution.decision_id == latest_decision.id)
                )
            ).all()
        )

    assert fake_executor.calls == [(seed.profile_id, seed.browser_host_name, seed.fb_ad_id)]
    assert processed_jobs == 1
    assert latest_decision.decision == DecisionType.WOULD_RESUME
    assert latest_decision.action_executed is True
    assert latest_decision.action_status == ActionExecutionStatus.SUCCEEDED.value
    assert len(jobs) == 1
    assert jobs[0].status == ActionJobStatus.SUCCEEDED
    assert len(action_executions) == 1
    assert action_executions[0].action_type == ActionType.RESUME
    assert action_executions[0].status == ActionExecutionStatus.SUCCEEDED
    assert action_executions[0].message == "Объявление снова запущено"
    assert ad.last_action_source == "авторезюм"
    assert ad.last_action_at is not None


# Проверяет, что worker восстанавливает источник автопаузы из истории успешных действий и после этого корректно делает авторезюм.
@pytest.mark.asyncio
async def test_worker_scan_service_restores_pause_owner_from_action_history(
    async_session_factory,
):
    seed = await seed_worker_ad_graph(async_session_factory)
    async with async_session_factory() as session:
        settings_repo = SystemSettingsRepository(session)
        await settings_repo.set_setting("auto_pause_enabled", "true")
        await settings_repo.set_setting("auto_resume_enabled", "true")
        await settings_repo.set_setting("observe_only_enabled", "false")
        await session.commit()

    await seed_offer_with_rate(
        async_session_factory,
        offer_code="offer-resume-history",
        offer_name="DRC_CR2",
        cpa_usd=Decimal("5.00"),
        effective_from=datetime(2026, 3, 20, 11, 0, tzinfo=UTC),
    )
    pause_row = WorkerScanRow(
        campaign_scope_key=seed.campaign_scope_key,
        campaign_name=seed.campaign_name,
        adset_scope_key=seed.adset_scope_key,
        adset_name=seed.adset_name,
        fb_ad_id=seed.fb_ad_id,
        ad_name="DRC_CR2_CR002",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.38"),
        clicks=4,
        cpc=Decimal("0.11"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
    )
    warmup_row = WorkerScanRow(
        campaign_scope_key=seed.campaign_scope_key,
        campaign_name=seed.campaign_name,
        adset_scope_key=seed.adset_scope_key,
        adset_name=seed.adset_name,
        fb_ad_id=seed.fb_ad_id,
        ad_name="DRC_CR2_CR002",
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.20"),
        clicks=2,
        cpc=Decimal("0.05"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=1,
        cost_per_registration=Decimal("0.20"),
        deposits=0,
        captured_at=datetime(2026, 3, 20, 12, 1, tzinfo=UTC),
    )
    resume_row = WorkerScanRow(
        campaign_scope_key=seed.campaign_scope_key,
        campaign_name=seed.campaign_name,
        adset_scope_key=seed.adset_scope_key,
        adset_name=seed.adset_name,
        fb_ad_id=seed.fb_ad_id,
        ad_name="DRC_CR2_CR002",
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.20"),
        clicks=2,
        cpc=Decimal("0.05"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=1,
        cost_per_registration=Decimal("0.20"),
        deposits=0,
        captured_at=datetime(2026, 3, 20, 12, 2, tzinfo=UTC),
    )

    fake_pause_executor = FakePauseExecutor()
    pause_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(rows=[pause_row]),
        pause_executor=fake_pause_executor,
        auto_pause_enabled=False,
        observe_only_enabled=True,
    )
    await pause_service.run_once(
        profile_id=seed.profile_id,
        browser_host_name=seed.browser_host_name,
    )
    await _run_action_queue(
        async_session_factory,
        pause_executor=fake_pause_executor,
    )

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        ad = await ads_repo.get_ad_by_fb_id(seed.fb_ad_id)
        assert ad is not None
        assert ad.last_action_source == "автопауза"
        ad.last_action_source = None
        ad.last_action_at = None
        await session.commit()

    warmup_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(rows=[warmup_row]),
    )
    await warmup_service.run_once(
        profile_id=seed.profile_id,
        browser_host_name=seed.browser_host_name,
    )

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        decisions_repo = DecisionsRepository(session)
        ad = await ads_repo.get_ad_by_fb_id(seed.fb_ad_id)
        decisions = await decisions_repo.list_decisions(fb_ad_id=seed.fb_ad_id, limit=1)
        assert ad is not None
        assert ad.last_action_source == "автопауза"
        assert ad.last_action_at is not None
        assert decisions[0].decision == DecisionType.KEPT_PAUSED_BY_VIABILITY
        assert decisions[0].reason == "Недостаточно чистых сканов подряд для безопасного включения"

    fake_resume_executor = FakeResumeExecutor()
    resume_service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(rows=[resume_row]),
        resume_executor=fake_resume_executor,
        auto_resume_enabled=False,
        observe_only_enabled=True,
    )
    await resume_service.run_once(
        profile_id=seed.profile_id,
        browser_host_name=seed.browser_host_name,
    )
    await _run_action_queue(
        async_session_factory,
        resume_executor=fake_resume_executor,
    )

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        decisions_repo = DecisionsRepository(session)
        ad = await ads_repo.get_ad_by_fb_id(seed.fb_ad_id)
        decisions = await decisions_repo.list_decisions(fb_ad_id=seed.fb_ad_id)
        latest_decision = decisions[0]
        action_executions = list(
            (
                await session.scalars(
                    select(ActionExecution).where(ActionExecution.decision_id == latest_decision.id)
                )
            ).all()
        )

    assert fake_pause_executor.calls == [(seed.profile_id, seed.browser_host_name, seed.fb_ad_id)]
    assert fake_resume_executor.calls == [(seed.profile_id, seed.browser_host_name, seed.fb_ad_id)]
    assert latest_decision.decision == DecisionType.WOULD_RESUME
    assert latest_decision.action_executed is True
    assert latest_decision.action_status == ActionExecutionStatus.SUCCEEDED.value
    assert len(action_executions) == 1
    assert action_executions[0].action_type == ActionType.RESUME
    assert action_executions[0].status == ActionExecutionStatus.SUCCEEDED
    assert ad is not None
    assert ad.last_action_source == "авторезюм"
    assert ad.last_action_at is not None
