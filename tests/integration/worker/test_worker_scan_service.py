from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.domain import (
    DecisionType,
    DeliveryStatus,
    EntityType,
    ScanRunStatus,
    ScopePresence,
    TrackingMode,
)
from core.models.advertising import MetricSnapshot
from core.repositories import DecisionsRepository, OffersRepository, ScanRunsRepository
from tests.fixtures.worker_scan_helpers import (
    FakeScannerProvider,
    WorkerScanRow,
    build_worker_service,
    seed_offer_with_binding,
    seed_worker_ad_graph,
)


# Проверяет, что дорогой клик по объявлению с валидной CPA сохраняет снимок и пишет решение `WOULD_PAUSE`.
@pytest.mark.asyncio
async def test_worker_scan_service_writes_pause_decision_for_expensive_click(async_session_factory):
    seed = await seed_worker_ad_graph(async_session_factory)
    await seed_offer_with_binding(
        async_session_factory,
        entity_type=EntityType.ADSET,
        entity_id=seed.adset_scope_key,
        offer_code="offer-1",
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
        binding = await OffersRepository(session).resolve_binding(None, seed.adset_scope_key)

    assert len(scan_runs) == 1
    assert scan_runs[0].status == ScanRunStatus.SUCCEEDED
    assert len(decisions) == 1
    assert decisions[0].decision == DecisionType.WOULD_PAUSE
    assert decisions[0].resolved_cpa_usd == Decimal("5.00")
    assert len(snapshots) == 1
    assert snapshots[0].fb_ad_id == seed.fb_ad_id
    assert snapshots[0].resolved_cpa_usd == Decimal("5.00")
    assert binding is not None
    assert binding.entity_id == seed.adset_scope_key


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
