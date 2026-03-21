from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from apps.worker.scan_service import WorkerScanService
from core.actions import BrowserActionResult
from core.domain import (
    ActionExecutionStatus,
    ActionType,
    DecisionType,
    DeliveryStatus,
    EntityType,
    ScanRunStatus,
    ScopePresence,
    TelegramEventType,
    TrackingMode,
)
from core.models.advertising import MetricSnapshot
from core.models.operations import ActionExecution, TelegramEvent
from core.repositories import (
    AdsRepository,
    BrowserRepository,
    DecisionsRepository,
    ScanRunsRepository,
    SystemSettingsRepository,
)
from tests.fixtures.worker_scan_helpers import (
    FakeScannerProvider,
    WorkerScanRow,
    build_worker_service,
    seed_offer_with_binding,
    seed_offer_with_rate,
    seed_worker_ad_graph,
)


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
    service = build_worker_service(
        async_session_factory=async_session_factory,
        scanner_provider=provider,
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

    assert len(scan_runs) == 1
    assert scan_runs[0].status == ScanRunStatus.SUCCEEDED
    assert len(decisions) == 1
    assert decisions[0].decision == DecisionType.WOULD_PAUSE
    assert decisions[0].resolved_cpa_usd == Decimal("5.00")
    assert str(decisions[0].offer_id) == str(offer_id)
    assert len(snapshots) == 1
    assert snapshots[0].fb_ad_id == seed.fb_ad_id
    assert snapshots[0].resolved_cpa_usd == Decimal("5.00")
    assert str(snapshots[0].offer_id) == str(offer_id)
    assert len(telegram_events) == 1
    assert telegram_events[0].event_type == TelegramEventType.OBSERVE_WOULD_PAUSE
    assert telegram_events[0].status == "pending"


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
    service = build_worker_service(
        async_session_factory=async_session_factory,
        scanner_provider=provider,
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
    service = build_worker_service(
        async_session_factory=async_session_factory,
        scanner_provider=initial_provider,
    )
    await service.run_once(profile_id=seed.profile_id, browser_host_name=seed.browser_host_name)

    empty_service = build_worker_service(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(rows=[]),
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

    primary_service = build_worker_service(
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
    )
    secondary_service = build_worker_service(
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
    )

    await primary_service.run_once(
        profile_id=primary_seed.profile_id,
        browser_host_name=primary_seed.browser_host_name,
    )
    await secondary_service.run_once(
        profile_id=profile.vendor_profile_id,
        browser_host_name=browser_host.name,
    )

    empty_primary_service = build_worker_service(
        async_session_factory=async_session_factory,
        scanner_provider=FakeScannerProvider(rows=[]),
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

    assert fake_executor.calls == [(seed.profile_id, seed.browser_host_name, seed.fb_ad_id)]
    assert len(decisions) == 1
    assert decisions[0].decision == DecisionType.WOULD_PAUSE
    assert decisions[0].action_executed is True
    assert decisions[0].action_status == ActionExecutionStatus.SUCCEEDED.value
    assert len(action_executions) == 1
    assert action_executions[0].action_type == ActionType.PAUSE
    assert action_executions[0].status == ActionExecutionStatus.SUCCEEDED
    assert action_executions[0].message == "Объявление переведено на паузу"


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
    assert decisions[0].action_status is None
    assert action_executions == []


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

    async with async_session_factory() as session:
        decisions_repo = DecisionsRepository(session)
        decisions = await decisions_repo.list_decisions(fb_ad_id=seed.fb_ad_id)
        latest_decision = decisions[0]
        action_executions = list(
            (
                await session.scalars(
                    select(ActionExecution).where(ActionExecution.decision_id == latest_decision.id)
                )
            ).all()
        )

    assert fake_executor.calls == [(seed.profile_id, seed.browser_host_name, seed.fb_ad_id)]
    assert latest_decision.decision == DecisionType.WOULD_RESUME
    assert latest_decision.action_executed is True
    assert latest_decision.action_status == ActionExecutionStatus.SUCCEEDED.value
    assert len(action_executions) == 1
    assert action_executions[0].action_type == ActionType.RESUME
    assert action_executions[0].status == ActionExecutionStatus.SUCCEEDED
    assert action_executions[0].message == "Объявление снова запущено"
