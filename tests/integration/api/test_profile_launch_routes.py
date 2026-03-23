from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from core.domain import (
    DecisionType,
    DeliveryStatus,
    EntityType,
    ScanRunStatus,
    ScopePresence,
    TrackingMode,
)
from core.models.operations import ControlFlag, Cooldown
from core.repositories import (
    AdsRepository,
    BrowserRepository,
    ControlFlagsRepository,
    DecisionsRepository,
    ProfileLaunchesRepository,
    ScanRunsRepository,
)


@pytest.fixture
async def api_client(async_session_factory, monkeypatch):
    from apps.api import bootstrap as api_bootstrap
    from apps.api import deps as api_deps
    from apps.api.main import app

    monkeypatch.setattr(api_deps, "get_session_factory", lambda: async_session_factory)
    monkeypatch.setattr(api_bootstrap, "get_session_factory", lambda: async_session_factory)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def _seed_profile_launch_filters(async_session_factory) -> dict[str, str]:
    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        launch_repo = ProfileLaunchesRepository(session)
        ads_repo = AdsRepository(session)
        decisions_repo = DecisionsRepository(session)
        scan_runs_repo = ScanRunsRepository(session)

        browser_host = await browser_repo.upsert_browser_host(
            name="vision-profile-launch-test",
            vendor="vision",
            api_base_url="http://127.0.0.1:3030",
            is_enabled=True,
            last_heartbeat_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
        )
        profile = await browser_repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="vision-profile-launch-test",
            display_name="Профиль запусков",
            is_active=True,
            last_launch_at=datetime(2026, 3, 23, 12, 5, tzinfo=UTC),
        )
        previous_launch = await launch_repo.ensure_active_profile_launch(profile.id)
        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign:profile-launch:test",
            name="Кампания запусков",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 23, 12, 6, tzinfo=UTC),
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset:profile-launch:test",
            campaign_id=campaign.id,
            name="Адсет запусков",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 23, 12, 7, tzinfo=UTC),
        )
        ad = await ads_repo.upsert_ad(
            fb_ad_id="120241420900000001",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="DRC_CR2_CR_LAUNCH",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 23, 12, 8, tzinfo=UTC),
        )
        previous_scan = await scan_runs_repo.create_scan_run(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status=ScanRunStatus.SUCCEEDED,
            started_at=datetime(2026, 3, 23, 12, 10, tzinfo=UTC),
            finished_at=datetime(2026, 3, 23, 12, 11, tzinfo=UTC),
            rows_seen=1,
            rows_parsed=1,
            scope_summary={"active_rows": 1, "paused_rows": 0},
            profile_launch_id=previous_launch.id,
        )
        await ads_repo.upsert_ad(
            fb_ad_id=ad.fb_ad_id,
            campaign_id=campaign.id,
            adset_id=adset.id,
            name=ad.name,
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 23, 12, 11, tzinfo=UTC),
            last_decision=DecisionType.WOULD_PAUSE,
            last_scan_run_id=previous_scan.id,
        )
        await ads_repo.add_metric_snapshot(
            fb_ad_id=ad.fb_ad_id,
            ad_id=ad.id,
            scan_run_id=previous_scan.id,
            captured_at=datetime(2026, 3, 23, 12, 11, tzinfo=UTC),
            spend=Decimal("0.11"),
            clicks=1,
            cpc=Decimal("0.11"),
            leads=0,
            cost_per_lead=None,
            registrations=0,
            cost_per_registration=None,
            deposits=0,
        )
        await decisions_repo.create_decision(
            scan_run_id=previous_scan.id,
            fb_ad_id=ad.fb_ad_id,
            decision=DecisionType.WOULD_PAUSE,
            reason="Старый запуск рекомендует паузу",
            ad_id=ad.id,
            resolved_cpa_usd=Decimal("5.00"),
            created_at=datetime(2026, 3, 23, 12, 11, tzinfo=UTC),
        )

        current_launch, _ = await launch_repo.start_new_profile_launch(profile.id, name="Запуск 2")
        current_scan = await scan_runs_repo.create_scan_run(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status=ScanRunStatus.SUCCEEDED,
            started_at=datetime(2026, 3, 23, 12, 20, tzinfo=UTC),
            finished_at=datetime(2026, 3, 23, 12, 21, tzinfo=UTC),
            rows_seen=1,
            rows_parsed=1,
            scope_summary={"active_rows": 0, "paused_rows": 1},
            profile_launch_id=current_launch.id,
        )
        await ads_repo.upsert_ad(
            fb_ad_id=ad.fb_ad_id,
            campaign_id=campaign.id,
            adset_id=adset.id,
            name=ad.name,
            delivery_status=DeliveryStatus.PAUSED,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 23, 12, 21, tzinfo=UTC),
            last_decision=DecisionType.NO_ACTION,
            last_scan_run_id=current_scan.id,
        )
        await ads_repo.add_metric_snapshot(
            fb_ad_id=ad.fb_ad_id,
            ad_id=ad.id,
            scan_run_id=current_scan.id,
            captured_at=datetime(2026, 3, 23, 12, 21, tzinfo=UTC),
            spend=Decimal("0.22"),
            clicks=2,
            cpc=Decimal("0.11"),
            leads=1,
            cost_per_lead=Decimal("0.22"),
            registrations=0,
            cost_per_registration=None,
            deposits=0,
        )
        await decisions_repo.create_decision(
            scan_run_id=current_scan.id,
            fb_ad_id=ad.fb_ad_id,
            decision=DecisionType.NO_ACTION,
            reason="Новый запуск оставляет без изменений",
            ad_id=ad.id,
            resolved_cpa_usd=Decimal("5.00"),
            created_at=datetime(2026, 3, 23, 12, 21, tzinfo=UTC),
        )
        await session.commit()
        return {
            "profile_id": profile.vendor_profile_id,
            "previous_launch_id": str(previous_launch.id),
            "current_launch_id": str(current_launch.id),
            "fb_ad_id": ad.fb_ad_id,
        }


async def _seed_profile_launch_reset(async_session_factory) -> dict[str, str]:
    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        launch_repo = ProfileLaunchesRepository(session)
        ads_repo = AdsRepository(session)
        flags_repo = ControlFlagsRepository(session)
        scan_runs_repo = ScanRunsRepository(session)

        browser_host = await browser_repo.upsert_browser_host(
            name="vision-profile-launch-reset",
            vendor="vision",
            api_base_url="http://127.0.0.1:3030",
            is_enabled=True,
            last_heartbeat_at=datetime(2026, 3, 23, 13, 0, tzinfo=UTC),
        )
        profile = await browser_repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="vision-profile-launch-reset",
            display_name="Профиль сброса запуска",
            is_active=True,
            last_launch_at=datetime(2026, 3, 23, 13, 5, tzinfo=UTC),
        )
        active_launch = await launch_repo.ensure_active_profile_launch(profile.id)
        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign:profile-launch:reset",
            name="Кампания сброса",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 23, 13, 6, tzinfo=UTC),
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset:profile-launch:reset",
            campaign_id=campaign.id,
            name="Адсет сброса",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 23, 13, 7, tzinfo=UTC),
        )
        scan_run = await scan_runs_repo.create_scan_run(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status=ScanRunStatus.SUCCEEDED,
            started_at=datetime(2026, 3, 23, 13, 8, tzinfo=UTC),
            finished_at=datetime(2026, 3, 23, 13, 9, tzinfo=UTC),
            rows_seen=1,
            rows_parsed=1,
            scope_summary={"active_rows": 1, "paused_rows": 0},
            profile_launch_id=active_launch.id,
        )
        ad = await ads_repo.upsert_ad(
            fb_ad_id="120241420900000009",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="DRC_CR2_CR_RESET",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.MANUAL_BLOCK,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 23, 13, 9, tzinfo=UTC),
            last_scan_run_id=scan_run.id,
        )
        await flags_repo.upsert_control_flag(
            entity_type=EntityType.AD,
            entity_id=ad.fb_ad_id,
            reason="Ручная блокировка старого запуска",
            created_by="pytest",
            tracking_mode=TrackingMode.MANUAL_BLOCK,
        )
        session.add(
            Cooldown(
                entity_type=EntityType.AD,
                entity_id=str(ad.id),
                until_at=datetime(2026, 3, 23, 14, 0, tzinfo=UTC),
                reason="Cooldown старого запуска",
            )
        )
        await session.commit()
        return {"profile_id": profile.vendor_profile_id, "active_launch_id": str(active_launch.id)}


async def _append_failed_scan_to_launch(async_session_factory, seed: dict[str, str]) -> None:
    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        scan_runs_repo = ScanRunsRepository(session)

        profile = await browser_repo.get_profile_by_vendor_id(seed["profile_id"])
        assert profile is not None
        browser_host = await browser_repo.get_browser_host(profile.browser_host_id)
        assert browser_host is not None

        await scan_runs_repo.create_scan_run(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status=ScanRunStatus.FAILED,
            started_at=datetime(2026, 3, 23, 12, 30, tzinfo=UTC),
            finished_at=datetime(2026, 3, 23, 12, 31, tzinfo=UTC),
            rows_seen=0,
            rows_parsed=0,
            error_message="Тестовый сбой скана",
            profile_launch_id=seed["current_launch_id"],
        )
        await session.commit()


# Проверяет, что новый запуск очищает только операционные флаги профиля и делает старый запуск архивным.
@pytest.mark.asyncio
async def test_create_profile_launch_resets_operational_state(
    api_client, async_session_factory
) -> None:
    seed = await _seed_profile_launch_reset(async_session_factory)
    client = api_client

    response = await client.post(
        "/profile-launches",
        json={"profile_id": seed["profile_id"]},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["message"] == "Новый запуск создан. Рабочее состояние профиля очищено."
    assert payload["cleared_control_flags"] == 1
    assert payload["cleared_cooldowns"] == 1
    assert payload["launch"]["is_active"] is True
    assert payload["launch"]["id"] != seed["active_launch_id"]

    async with async_session_factory() as session:
        launch_repo = ProfileLaunchesRepository(session)
        launches = await launch_repo.list_profile_launches(
            (await BrowserRepository(session).get_profile_by_vendor_id(seed["profile_id"])).id
        )
        flags_count = await session.scalar(select(func.count()).select_from(ControlFlag))
        cooldowns_count = await session.scalar(select(func.count()).select_from(Cooldown))

    assert launches[0].is_active is True
    assert launches[1].is_active is False
    assert int(flags_count or 0) == 0
    assert int(cooldowns_count or 0) == 0


# Проверяет, что фильтры по profile_launch_id возвращают данные только выбранного запуска.
@pytest.mark.asyncio
async def test_profile_launch_filters_isolate_ads_decisions_and_scans(
    api_client,
    async_session_factory,
) -> None:
    seed = await _seed_profile_launch_filters(async_session_factory)
    client = api_client

    ads_previous = await client.get(
        "/ads",
        params={
            "profile_id": seed["profile_id"],
            "profile_launch_id": seed["previous_launch_id"],
        },
    )
    ads_current = await client.get(
        "/ads",
        params={
            "profile_id": seed["profile_id"],
            "profile_launch_id": seed["current_launch_id"],
        },
    )
    decisions_previous = await client.get(
        "/decisions",
        params={
            "profile_id": seed["profile_id"],
            "profile_launch_id": seed["previous_launch_id"],
        },
    )
    scans_previous = await client.get(
        "/scan-runs",
        params={
            "profile_id": seed["profile_id"],
            "profile_launch_id": seed["previous_launch_id"],
        },
    )

    assert ads_previous.status_code == 200
    assert ads_current.status_code == 200
    assert decisions_previous.status_code == 200
    assert scans_previous.status_code == 200

    previous_payload = ads_previous.json()
    current_payload = ads_current.json()
    assert len(previous_payload) == 1
    assert len(current_payload) == 1
    assert previous_payload[0]["fb_ad_id"] == seed["fb_ad_id"]
    assert previous_payload[0]["last_decision"] == "WOULD_PAUSE"
    assert previous_payload[0]["spend"] == "0.11"
    assert current_payload[0]["last_decision"] == "NO_ACTION"
    assert current_payload[0]["spend"] == "0.22"

    decisions_payload = decisions_previous.json()
    assert len(decisions_payload) == 1
    assert decisions_payload[0]["decision"] == "WOULD_PAUSE"

    scans_payload = scans_previous.json()
    assert len(scans_payload) == 1
    assert scans_payload[0]["profile_launch_id"] == seed["previous_launch_id"]


# Проверяет, что список запусков отдаёт текущий запуск первым и сохраняет архив профиля.
@pytest.mark.asyncio
async def test_list_profile_launches_returns_active_first(
    api_client, async_session_factory
) -> None:
    seed = await _seed_profile_launch_filters(async_session_factory)
    client = api_client

    response = await client.get(
        "/profile-launches",
        params={"profile_id": seed["profile_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["id"] == seed["current_launch_id"]
    assert payload[0]["is_active"] is True
    assert payload[1]["id"] == seed["previous_launch_id"]
    assert payload[1]["is_active"] is False


# Проверяет, что detail объявления читает метрики и решения именно внутри выбранного запуска.
@pytest.mark.asyncio
async def test_ad_detail_respects_profile_launch_filter(api_client, async_session_factory) -> None:
    seed = await _seed_profile_launch_filters(async_session_factory)
    client = api_client

    previous_detail = await client.get(
        f"/ads/{seed['fb_ad_id']}",
        params={"profile_launch_id": seed["previous_launch_id"]},
    )
    current_detail = await client.get(
        f"/ads/{seed['fb_ad_id']}",
        params={"profile_launch_id": seed["current_launch_id"]},
    )

    assert previous_detail.status_code == 200
    assert current_detail.status_code == 200
    assert previous_detail.json()["last_decision"] == "WOULD_PAUSE"
    assert previous_detail.json()["spend"] == "0.11"
    assert current_detail.json()["last_decision"] == "NO_ACTION"
    assert current_detail.json()["spend"] == "0.22"


# Проверяет, что сводка запуска и тренды игнорируют failed-сканы и не загрязняются нулями.
@pytest.mark.asyncio
async def test_profile_launch_dashboard_uses_only_successful_scans_for_summary_and_trends(
    api_client,
    async_session_factory,
) -> None:
    seed = await _seed_profile_launch_filters(async_session_factory)
    await _append_failed_scan_to_launch(async_session_factory, seed)
    client = api_client

    response = await client.get(f"/profile-launches/{seed['current_launch_id']}/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current"]["last_scan_at"] == "2026-03-23T12:21:00Z"
    assert payload["current"]["spend_total"] == "0.22"
    assert len(payload["spend_series"]) == 1
    assert payload["spend_series"][0]["value"] == "0.22"
    assert len(payload["attention_series"]) == 1
    assert len(payload["action_series"]) == 1
