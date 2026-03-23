from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from apps.browser_host.adapters.models import AdapterHealth, AutomationLaunchResult, ProfileStatus
from core.config import get_settings as get_cached_settings
from core.domain import (
    ActionType,
    DecisionType,
    DeliveryStatus,
    EntityType,
    RiskBand,
    ScanPipelineKind,
    ScanRunStatus,
    ScopePresence,
    TrackingMode,
)
from core.repositories import (
    ActionJobsRepository,
    AdsRepository,
    BrowserRepository,
    DecisionsRepository,
    OffersRepository,
    ScanRunsRepository,
    WatchlistRepository,
)


class FakeVisionAdapter:
    """Фейковый адаптер Vision для проверки API без реального браузера."""

    def __init__(self) -> None:
        self.started_profiles: list[str] = []
        self.stopped_profiles: list[str] = []

    async def list_profiles(self) -> list:
        return []

    async def list_open_profiles(self) -> list:
        return []

    async def get_profile_status(self, profile_id: str) -> ProfileStatus:
        return ProfileStatus(
            profile_id=profile_id,
            state="STOPPED",
            has_automation_binding=False,
        )

    async def stop_profile(self, profile_id: str) -> None:
        self.stopped_profiles.append(profile_id)

    async def start_profile_for_automation(
        self,
        profile_id: str,
        launch_mode: str,
        launch_args: list[str] | None = None,
    ) -> AutomationLaunchResult:
        self.started_profiles.append(profile_id)
        return AutomationLaunchResult(
            profile_id=profile_id,
            vendor="vision",
            cdp_url="http://127.0.0.1:54000",
            webdriver_url=None,
            debug_port=54000,
            browser_pid=4242,
            launched_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )

    async def healthcheck(self) -> AdapterHealth:
        return AdapterHealth(is_healthy=True, message="Vision доступен")


class FakeSessionManager:
    """Фейковый session manager для проверки нового lifecycle запуска сессии через API."""

    def __init__(self, adapter: FakeVisionAdapter) -> None:
        self._adapter = adapter

    async def ensure_profile_started(self, profile_id: str) -> AutomationLaunchResult:
        status = await self._adapter.get_profile_status(profile_id)
        if status.has_automation_binding:
            pass
        elif status.state == "RUNNING":
            await self._adapter.stop_profile(profile_id)
        return await self._adapter.start_profile_for_automation(
            profile_id=profile_id,
            launch_mode="cdp",
            launch_args=[],
        )


@pytest.fixture
async def api_client(async_session_factory, monkeypatch):
    from apps.api import bootstrap as api_bootstrap
    from apps.api import deps as api_deps
    from apps.api.main import app
    from apps.api.routers import sessions as sessions_router
    from apps.api.services import health as health_service

    monkeypatch.setenv("FEATURE_AUTO_RESUME", "false")
    get_cached_settings.cache_clear()
    fake_adapter = FakeVisionAdapter()
    fake_session_manager = FakeSessionManager(fake_adapter)
    monkeypatch.setattr(api_deps, "get_session_factory", lambda: async_session_factory)
    monkeypatch.setattr(api_bootstrap, "get_session_factory", lambda: async_session_factory)
    monkeypatch.setattr(sessions_router, "build_adapter", lambda settings: fake_adapter)
    monkeypatch.setattr(
        sessions_router,
        "build_session_manager",
        lambda settings: fake_session_manager,
    )

    async def fake_check_database_connection() -> bool:
        return True

    monkeypatch.setattr(health_service, "check_database_connection", fake_check_database_connection)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, fake_adapter, fake_session_manager


async def _seed_operational_data(async_session_factory) -> None:
    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        scan_runs_repo = ScanRunsRepository(session)
        decisions_repo = DecisionsRepository(session)

        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign:account-1:campaign-1",
            name="Кампания 1",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset:campaign:account-1:campaign-1:adset-1",
            campaign_id=campaign.id,
            name="Адсет 1",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 10, 1, tzinfo=UTC),
        )
        ad = await ads_repo.upsert_ad(
            fb_ad_id="ad-1",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="Объявление 1",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 20, 10, 2, tzinfo=UTC),
        )
        scan_run = await scan_runs_repo.create_scan_run(
            browser_host_id=None,
            profile_id=None,
            status=ScanRunStatus.SUCCEEDED,
            started_at=datetime(2026, 3, 20, 10, 3, tzinfo=UTC),
            finished_at=datetime(2026, 3, 20, 10, 4, tzinfo=UTC),
            rows_seen=1,
            rows_parsed=1,
            scope_summary={"ads": 1},
        )
        await decisions_repo.create_decision(
            scan_run_id=scan_run.id,
            fb_ad_id="ad-1",
            decision=DecisionType.WOULD_PAUSE,
            reason="Тестовое решение",
            resolved_cpa_usd=Decimal("5.00"),
        )
        await ads_repo.add_metric_snapshot(
            fb_ad_id="ad-1",
            ad_id=ad.id,
            scan_run_id=scan_run.id,
            captured_at=datetime(2026, 3, 20, 10, 5, tzinfo=UTC),
            spend=Decimal("12.50"),
            clicks=25,
            cpc=Decimal("0.50"),
            leads=4,
            cost_per_lead=Decimal("3.12"),
            registrations=2,
            cost_per_registration=Decimal("6.25"),
            deposits=1,
        )
        await session.commit()


async def _seed_scan_run_with_browser_links(async_session_factory) -> None:
    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        scan_runs_repo = ScanRunsRepository(session)

        browser_host = await browser_repo.upsert_browser_host(
            name="vision-3030",
            vendor="vision",
            api_base_url="http://127.0.0.1:3030",
            is_enabled=True,
            last_heartbeat_at=datetime(2026, 3, 21, 20, 0, tzinfo=UTC),
        )
        profile = await browser_repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="vision-profile-readable",
            display_name="Vision профиль readable",
            is_active=True,
            last_launch_at=datetime(2026, 3, 21, 20, 1, tzinfo=UTC),
        )
        await scan_runs_repo.create_scan_run(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status=ScanRunStatus.FAILED,
            started_at=datetime(2026, 3, 21, 20, 2, tzinfo=UTC),
            finished_at=datetime(2026, 3, 21, 20, 3, tzinfo=UTC),
            rows_seen=0,
            rows_parsed=0,
            error_message="Тестовая ошибка сканирования",
        )
        await session.commit()


async def _seed_ads_with_stale_state(async_session_factory) -> dict[str, str]:
    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        ads_repo = AdsRepository(session)
        scan_runs_repo = ScanRunsRepository(session)

        browser_host = await browser_repo.upsert_browser_host(
            name="vision-stale",
            vendor="vision",
            api_base_url="http://127.0.0.1:3030",
            is_enabled=True,
            last_heartbeat_at=datetime(2026, 3, 22, 11, 0, tzinfo=UTC),
        )
        profile = await browser_repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="vision-profile-stale",
            display_name="Vision профиль stale",
            is_active=True,
            last_launch_at=datetime(2026, 3, 22, 11, 1, tzinfo=UTC),
        )
        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign:vision-stale:campaign-1",
            name="Кампания stale",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 22, 11, 2, tzinfo=UTC),
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset:vision-stale:campaign-1:adset-1",
            campaign_id=campaign.id,
            name="Адсет stale",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 22, 11, 3, tzinfo=UTC),
        )
        scan_run = await scan_runs_repo.create_scan_run(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status=ScanRunStatus.SUCCEEDED,
            started_at=datetime(2026, 3, 22, 11, 4, tzinfo=UTC),
            finished_at=datetime(2026, 3, 22, 11, 5, tzinfo=UTC),
            rows_seen=2,
            rows_parsed=2,
            scope_summary={
                "rows_seen": 2,
                "rows_in_scope": 2,
                "rows_not_seen_this_scan": 0,
                "rows_out_of_scope_confirmed": 0,
                "active_rows": 1,
                "paused_rows": 1,
                "not_delivering_rows": 0,
                "manual_blocked_rows": 0,
                "read_only_rows": 0,
                "unknown_rows": 0,
                "scanned_at": "2026-03-22T11:05:00Z",
                "fb_ad_ids": ["fresh-ad", "stale-ad"],
            },
        )
        fresh_ad = await ads_repo.upsert_ad(
            fb_ad_id="fresh-ad",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="DRC_CR2_CR101",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 22, 11, 5, tzinfo=UTC),
            last_scan_run_id=scan_run.id,
        )
        stale_ad = await ads_repo.upsert_ad(
            fb_ad_id="stale-ad",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="DRC_CR2_CR102",
            delivery_status=DeliveryStatus.PAUSED,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 22, 11, 5, tzinfo=UTC),
            last_scan_run_id=scan_run.id,
        )
        await ads_repo.add_metric_snapshot(
            fb_ad_id=fresh_ad.fb_ad_id,
            ad_id=fresh_ad.id,
            scan_run_id=scan_run.id,
            captured_at=datetime(2026, 3, 22, 11, 5, tzinfo=UTC),
            spend=Decimal("1.25"),
            clicks=12,
            cpc=Decimal("0.10"),
            leads=1,
            cost_per_lead=Decimal("1.25"),
            registrations=0,
            cost_per_registration=Decimal("0.00"),
            deposits=0,
        )
        await ads_repo.add_metric_snapshot(
            fb_ad_id=stale_ad.fb_ad_id,
            ad_id=stale_ad.id,
            scan_run_id=scan_run.id,
            captured_at=datetime(2026, 3, 22, 11, 5, tzinfo=UTC),
            spend=Decimal("0.75"),
            clicks=4,
            cpc=Decimal("0.19"),
            leads=0,
            cost_per_lead=Decimal("0.00"),
            registrations=0,
            cost_per_registration=Decimal("0.00"),
            deposits=0,
        )
        await ads_repo.mark_unseen_ads(seen_fb_ad_ids=[fresh_ad.fb_ad_id], profile_id=profile.id)
        await session.commit()

    return {
        "fresh_ad_id": "fresh-ad",
        "stale_ad_id": "stale-ad",
    }


async def _seed_scan_run_with_scope_summary(async_session_factory) -> None:
    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        scan_runs_repo = ScanRunsRepository(session)

        browser_host = await browser_repo.upsert_browser_host(
            name="vision-summary",
            vendor="vision",
            api_base_url="http://127.0.0.1:3030",
            is_enabled=True,
            last_heartbeat_at=datetime(2026, 3, 22, 12, 0, tzinfo=UTC),
        )
        profile = await browser_repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="vision-profile-summary",
            display_name="Vision профиль summary",
            is_active=True,
            last_launch_at=datetime(2026, 3, 22, 12, 1, tzinfo=UTC),
        )
        await scan_runs_repo.create_scan_run(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status=ScanRunStatus.SUCCEEDED,
            started_at=datetime(2026, 3, 22, 12, 2, tzinfo=UTC),
            finished_at=datetime(2026, 3, 22, 12, 3, tzinfo=UTC),
            rows_seen=3,
            rows_parsed=3,
            scope_summary={
                "rows_seen": 3,
                "rows_in_scope": 3,
                "rows_not_seen_this_scan": 0,
                "rows_out_of_scope_confirmed": 0,
                "active_rows": 2,
                "paused_rows": 1,
                "not_delivering_rows": 0,
                "manual_blocked_rows": 0,
                "read_only_rows": 0,
                "unknown_rows": 0,
                "scanned_at": "2026-03-22T12:03:00Z",
                "fb_ad_ids": ["ad-1", "ad-2", "ad-3"],
            },
        )
        await session.commit()


async def _seed_decisions_with_execution_states(async_session_factory) -> None:
    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        scan_runs_repo = ScanRunsRepository(session)
        decisions_repo = DecisionsRepository(session)

        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign:execution-state:campaign-1",
            name="Кампания execution state",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 22, 13, 0, tzinfo=UTC),
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset:execution-state:campaign-1:adset-1",
            campaign_id=campaign.id,
            name="Адсет execution state",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 22, 13, 1, tzinfo=UTC),
        )
        await ads_repo.upsert_ad(
            fb_ad_id="decision-no-action",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="DRC_CR2_CR201",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 22, 13, 2, tzinfo=UTC),
        )
        await ads_repo.upsert_ad(
            fb_ad_id="decision-skip",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="DRC_CR2_CR202",
            delivery_status=DeliveryStatus.PAUSED,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 22, 13, 3, tzinfo=UTC),
        )
        await ads_repo.upsert_ad(
            fb_ad_id="decision-succeeded",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="DRC_CR2_CR203",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 22, 13, 4, tzinfo=UTC),
        )
        await ads_repo.upsert_ad(
            fb_ad_id="decision-failed",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="DRC_CR2_CR204",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 22, 13, 5, tzinfo=UTC),
        )
        scan_run = await scan_runs_repo.create_scan_run(
            browser_host_id=None,
            profile_id=None,
            status=ScanRunStatus.SUCCEEDED,
            started_at=datetime(2026, 3, 22, 13, 6, tzinfo=UTC),
            finished_at=datetime(2026, 3, 22, 13, 7, tzinfo=UTC),
            rows_seen=4,
            rows_parsed=4,
            scope_summary={"rows_seen": 4, "fb_ad_ids": []},
        )
        await decisions_repo.create_decision(
            scan_run_id=scan_run.id,
            fb_ad_id="decision-no-action",
            decision=DecisionType.NO_ACTION,
            reason="Нет действия",
            resolved_cpa_usd=Decimal("5.00"),
            created_at=datetime(2026, 3, 22, 13, 8, tzinfo=UTC),
        )
        await decisions_repo.create_decision(
            scan_run_id=scan_run.id,
            fb_ad_id="decision-skip",
            decision=DecisionType.WOULD_PAUSE,
            reason="Пропуск по режиму",
            resolved_cpa_usd=Decimal("5.00"),
            created_at=datetime(2026, 3, 22, 13, 9, tzinfo=UTC),
        )
        await decisions_repo.create_decision(
            scan_run_id=scan_run.id,
            fb_ad_id="decision-succeeded",
            decision=DecisionType.WOULD_RESUME,
            reason="Успешное действие",
            resolved_cpa_usd=Decimal("5.00"),
            action_executed=True,
            action_status="SUCCEEDED",
            created_at=datetime(2026, 3, 22, 13, 10, tzinfo=UTC),
        )
        await decisions_repo.create_decision(
            scan_run_id=scan_run.id,
            fb_ad_id="decision-failed",
            decision=DecisionType.WOULD_PAUSE,
            reason="Ошибка действия",
            resolved_cpa_usd=Decimal("5.00"),
            action_executed=False,
            action_status="FAILED",
            created_at=datetime(2026, 3, 22, 13, 11, tzinfo=UTC),
        )
        await session.commit()


# Проверяет, что operational-роуты читают и обновляют реальные данные из базы вместо demo-state.
@pytest.mark.asyncio
async def test_operational_routes_use_database(api_client, async_session_factory) -> None:
    client, _, _ = api_client
    await _seed_operational_data(async_session_factory)

    ads_response = await client.get("/ads")
    decisions_response = await client.get("/decisions")
    scan_runs_response = await client.get("/scan-runs")

    assert ads_response.status_code == 200
    assert decisions_response.status_code == 200
    assert scan_runs_response.status_code == 200
    assert ads_response.json()[0]["fb_ad_id"] == "ad-1"
    assert ads_response.json()[0]["campaign_name"] == "Кампания 1"
    assert ads_response.json()[0]["spend"] == "12.50"
    assert ads_response.json()[0]["clicks"] == 25
    assert ads_response.json()[0]["cpc"] == "0.50"
    assert ads_response.json()[0]["deposits"] == 1
    assert ads_response.json()[0]["last_decision_reason"] == "Тестовое решение"
    assert ads_response.json()[0]["last_execution_state"] == "SKIPPED_BY_MODE"
    assert decisions_response.json()[0]["reason"] == "Тестовое решение"
    assert decisions_response.json()[0]["execution_state"] == "SKIPPED_BY_MODE"
    assert scan_runs_response.json()[0]["rows_seen"] == 1
    assert scan_runs_response.json()[0]["browser_host_id"] == ""
    assert scan_runs_response.json()[0]["profile_id"] == ""
    assert scan_runs_response.json()[0]["scope_summary"]["ads"] == 1

    ad_detail_response = await client.get("/ads/ad-1")
    assert ad_detail_response.status_code == 200
    assert ad_detail_response.json()["campaign_scope_key"] == "campaign:account-1:campaign-1"
    assert ad_detail_response.json()["cost_per_lead"] == "3.12"
    assert ad_detail_response.json()["registrations"] == 2
    assert (
        ad_detail_response.json()["adset_scope_key"]
        == "adset:campaign:account-1:campaign-1:adset-1"
    )

    block_response = await client.post(
        "/ads/ad-1/block",
        json={"reason": "Ручная блокировка", "created_by": "оператор"},
    )
    flags_response = await client.get("/control-flags")
    unblock_response = await client.post("/ads/ad-1/unblock")
    flags_after_unblock = await client.get("/control-flags")

    assert block_response.status_code == 200
    assert block_response.json()["ad"]["tracking_mode"] == "MANUAL_BLOCK"
    assert block_response.json()["ad"]["last_action_source"] == "оператор"
    assert flags_response.status_code == 200
    assert len(flags_response.json()) == 1
    assert unblock_response.status_code == 200
    assert unblock_response.json()["ad"]["tracking_mode"] == "TRACKED"
    assert unblock_response.json()["ad"]["last_action_source"] == "operator"
    assert flags_after_unblock.json() == []


# Проверяет, что системные правила поднимаются из базы на старте и обновляются через API без in-memory слоя.
@pytest.mark.asyncio
async def test_rules_routes_work_through_database(api_client) -> None:
    client, _, _ = api_client

    rules_response = await client.get("/rules")

    assert rules_response.status_code == 200
    rules = rules_response.json()
    assert len(rules) == 6
    assert rules[0]["code"] == "stop_high_cpc"

    rule_id = rules[0]["id"]
    update_response = await client.put(
        f"/rules/{rule_id}",
        json={
            "title": "Стоп по клику обновлен",
            "priority": 15,
            "cpa_multiplier": "0.03",
        },
    )
    refreshed_rules = await client.get("/rules")

    assert update_response.status_code == 200
    assert update_response.json()["title"] == "Стоп по клику обновлен"
    assert update_response.json()["priority"] == 15
    assert update_response.json()["cpa_multiplier"] == "0.03"
    assert refreshed_rules.json()[0]["title"] == "Стоп по клику обновлен"


# Проверяет, что scan-runs API возвращает читаемые имя хоста и vendor profile id вместо UUID базы.
@pytest.mark.asyncio
async def test_scan_runs_route_returns_readable_browser_host_and_profile(
    api_client, async_session_factory
) -> None:
    client, _, _ = api_client
    await _seed_scan_run_with_browser_links(async_session_factory)

    response = await client.get("/scan-runs")

    assert response.status_code == 200
    assert response.json()[0]["browser_host_id"] == "vision-3030"
    assert response.json()[0]["profile_id"] == "vision-profile-readable"
    assert response.json()[0]["error_message"] == "Тестовая ошибка сканирования"


# Проверяет, что scan-runs API отдает полный scope_summary, включая fb_ad_ids и счетчики строк.
@pytest.mark.asyncio
async def test_scan_runs_route_returns_scope_summary_payload(
    api_client, async_session_factory
) -> None:
    client, _, _ = api_client
    await _seed_scan_run_with_scope_summary(async_session_factory)

    response = await client.get("/scan-runs")

    assert response.status_code == 200
    scope_summary = response.json()[0]["scope_summary"]
    assert scope_summary["rows_seen"] == 3
    assert scope_summary["rows_in_scope"] == 3
    assert scope_summary["paused_rows"] == 1
    assert scope_summary["fb_ad_ids"] == ["ad-1", "ad-2", "ad-3"]


# Проверяет, что настройки режима бота читаются и сохраняются через базу данных.
@pytest.mark.asyncio
async def test_settings_routes_persist_bot_mode(api_client) -> None:
    client, _, _ = api_client

    initial_response = await client.get("/settings/bot-mode")
    update_response = await client.put(
        "/settings/bot-mode",
        json={
            "auto_pause_enabled": True,
            "auto_resume_enabled": False,
            "observe_only_enabled": True,
        },
    )
    refreshed_response = await client.get("/settings/bot-mode")

    assert initial_response.status_code == 200
    assert update_response.status_code == 200
    assert refreshed_response.status_code == 200
    assert update_response.json()["auto_pause_enabled"] is True
    assert update_response.json()["auto_resume_enabled"] is False
    assert update_response.json()["observe_only_enabled"] is True
    assert refreshed_response.json()["auto_pause_enabled"] is True
    assert refreshed_response.json()["auto_resume_enabled"] is False
    assert refreshed_response.json()["observe_only_enabled"] is True


# Проверяет, что stale-объявление сохраняется в API как не увиденное в последнем скане, а метрики не теряются.
@pytest.mark.asyncio
async def test_ads_route_marks_unseen_ads_as_stale(api_client, async_session_factory) -> None:
    client, _, _ = api_client
    ad_ids = await _seed_ads_with_stale_state(async_session_factory)

    response = await client.get("/ads")
    stale_detail_response = await client.get(f"/ads/{ad_ids['stale_ad_id']}")

    assert response.status_code == 200
    fresh_ad = next(item for item in response.json() if item["fb_ad_id"] == ad_ids["fresh_ad_id"])
    stale_ad = next(item for item in response.json() if item["fb_ad_id"] == ad_ids["stale_ad_id"])
    assert fresh_ad["scope_presence"] == "IN_SCOPE"
    assert fresh_ad["spend"] == "1.25"
    assert stale_ad["scope_presence"] == "NOT_SEEN_THIS_SCAN"
    assert stale_ad["spend"] == "0.75"
    assert stale_detail_response.status_code == 200
    assert stale_detail_response.json()["scope_presence"] == "NOT_SEEN_THIS_SCAN"
    assert stale_detail_response.json()["last_decision"] == "NO_ACTION"


# Проверяет, что service settings читаются из базы, маскируют секреты и сохраняют whitelist частоты скана.
@pytest.mark.asyncio
async def test_service_settings_routes_persist_runtime_values(api_client) -> None:
    client, _, _ = api_client

    initial_response = await client.get("/settings/service")
    update_response = await client.put(
        "/settings/service",
        json={
            "auto_pause_enabled": True,
            "auto_resume_enabled": True,
            "observe_only_enabled": False,
            "full_scan_interval_seconds": 60,
            "recheck_interval_seconds": 15,
            "full_scan_profile_concurrency": 2,
            "action_worker_concurrency": 2,
            "vision_local_api_url": "http://127.0.0.1:4040",
            "vision_cloud_api_url": "https://vision.example/api",
            "telegram_chat_id": "777000",
            "vision_api_token": "vision-secret-token",
            "telegram_bot_token": "telegram-secret-token",
        },
    )
    refreshed_response = await client.get("/settings/service")

    assert initial_response.status_code == 200
    assert update_response.status_code == 200
    assert refreshed_response.status_code == 200
    assert update_response.json()["auto_pause_enabled"] is True
    assert (
        update_response.json()["auto_resume_enabled"]
        is update_response.json()["auto_resume_available"]
    )
    assert (
        refreshed_response.json()["auto_resume_enabled"]
        is refreshed_response.json()["auto_resume_available"]
    )
    assert (
        update_response.json()["auto_resume_available"]
        is initial_response.json()["auto_resume_available"]
    )
    assert update_response.json()["observe_only_enabled"] is False
    assert update_response.json()["full_scan_interval_seconds"] == 60
    assert update_response.json()["recheck_interval_seconds"] == 15
    assert update_response.json()["full_scan_profile_concurrency"] == 2
    assert update_response.json()["action_worker_concurrency"] == 2
    assert update_response.json()["vision_local_api_url"] == "http://127.0.0.1:4040"
    assert update_response.json()["vision_cloud_api_url"] == "https://vision.example/api"
    assert update_response.json()["telegram_chat_id"] == "777000"
    assert update_response.json()["vision_api_token_configured"] is True
    assert update_response.json()["telegram_bot_token_configured"] is True
    assert update_response.json()["vision_api_token_masked"].endswith("oken")
    assert update_response.json()["telegram_bot_token_masked"].endswith("oken")
    assert refreshed_response.json()["full_scan_interval_seconds"] == 60
    assert refreshed_response.json()["vision_local_api_url"] == "http://127.0.0.1:4040"


# Проверяет, что /decisions отдает нормализованный execution_state для всех основных сценариев.
@pytest.mark.asyncio
async def test_decisions_route_exposes_execution_state_contract(
    api_client, async_session_factory
) -> None:
    client, _, _ = api_client
    await _seed_decisions_with_execution_states(async_session_factory)

    response = await client.get("/decisions")

    assert response.status_code == 200
    decisions_by_ad_id = {item["fb_ad_id"]: item for item in response.json()}
    assert decisions_by_ad_id["decision-no-action"]["execution_state"] == "NOT_REQUIRED"
    assert decisions_by_ad_id["decision-skip"]["execution_state"] == "SKIPPED_BY_MODE"
    assert decisions_by_ad_id["decision-succeeded"]["execution_state"] == "SUCCEEDED"
    assert decisions_by_ad_id["decision-failed"]["execution_state"] == "FAILED"


# Проверяет, что service settings отклоняют неподдерживаемую частоту скана корректной клиентской ошибкой.
@pytest.mark.asyncio
async def test_service_settings_routes_reject_invalid_scan_interval(api_client) -> None:
    client, _, _ = api_client

    response = await client.put(
        "/settings/service",
        json={
            "auto_pause_enabled": True,
            "auto_resume_enabled": False,
            "observe_only_enabled": False,
            "full_scan_interval_seconds": 45,
            "recheck_interval_seconds": 15,
            "full_scan_profile_concurrency": 2,
            "action_worker_concurrency": 2,
            "vision_local_api_url": "http://127.0.0.1:4040",
            "vision_cloud_api_url": "https://vision.example/api",
            "telegram_chat_id": "",
            "vision_api_token": None,
            "telegram_bot_token": None,
        },
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"] == "Допустимая частота полного скана: 30, 60, 120 или 300 секунд"
    )


# Проверяет, что watchlist API отдает риск, причину и fast-stop состояние по объявлению.
@pytest.mark.asyncio
async def test_watchlist_route_returns_fast_stop_items(api_client, async_session_factory) -> None:
    client, _, _ = api_client

    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        ads_repo = AdsRepository(session)
        scan_repo = ScanRunsRepository(session)
        watchlist_repo = WatchlistRepository(session)

        browser_host = await browser_repo.upsert_browser_host(
            name="vision-watchlist-route",
            vendor="vision",
            api_base_url="http://127.0.0.1:3030",
            is_enabled=True,
            last_heartbeat_at=datetime(2026, 3, 23, 10, 0, tzinfo=UTC),
        )
        profile = await browser_repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="vision-watchlist-profile",
            display_name="Vision watchlist profile",
            is_active=True,
            last_launch_at=datetime(2026, 3, 23, 10, 1, tzinfo=UTC),
        )
        campaign = await ads_repo.upsert_campaign(scope_key="campaign:watchlist", name="Кампания")
        adset = await ads_repo.upsert_adset(
            scope_key="adset:campaign:watchlist:1",
            campaign_id=campaign.id,
            name="Адсет",
        )
        ad = await ads_repo.upsert_ad(
            fb_ad_id="watchlist-ad-1",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="Объявление watchlist",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            risk_band=RiskBand.WATCH,
            last_risk_reason="Рядом со стопом",
            last_seen_at=datetime(2026, 3, 23, 10, 2, tzinfo=UTC),
        )
        scan_run = await scan_repo.create_scan_run(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status=ScanRunStatus.SUCCEEDED,
            started_at=datetime(2026, 3, 23, 10, 2, tzinfo=UTC),
            pipeline_kind=ScanPipelineKind.FULL_SCAN,
        )
        await watchlist_repo.upsert_entry(
            ad_id=ad.id,
            fb_ad_id=ad.fb_ad_id,
            profile_id=profile.id,
            browser_host_id=browser_host.id,
            risk_band=RiskBand.WATCH,
            priority_score=640,
            next_check_at=datetime(2026, 3, 23, 10, 3, tzinfo=UTC),
            last_reason="Рядом со стопом",
            last_metrics_at=datetime(2026, 3, 23, 10, 2, tzinfo=UTC),
            source_scan_run_id=scan_run.id,
        )
        await session.commit()

    response = await client.get("/watchlist")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["fb_ad_id"] == "watchlist-ad-1"
    assert payload[0]["risk_band"] == "WATCH"
    assert payload[0]["watch_reason"] == "Рядом со стопом"
    assert payload[0]["fast_stop_state"] == "WATCH"


# Проверяет, что action-jobs API отдает очередь действий с именами кампании и профиля.
@pytest.mark.asyncio
async def test_action_jobs_route_returns_queue_items(api_client, async_session_factory) -> None:
    client, _, _ = api_client

    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        ads_repo = AdsRepository(session)
        scan_repo = ScanRunsRepository(session)
        decisions_repo = DecisionsRepository(session)
        jobs_repo = ActionJobsRepository(session)

        browser_host = await browser_repo.upsert_browser_host(
            name="vision-action-jobs-route",
            vendor="vision",
            api_base_url="http://127.0.0.1:3030",
            is_enabled=True,
            last_heartbeat_at=datetime(2026, 3, 23, 11, 0, tzinfo=UTC),
        )
        profile = await browser_repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="vision-action-profile",
            display_name="Vision action profile",
            is_active=True,
            last_launch_at=datetime(2026, 3, 23, 11, 1, tzinfo=UTC),
        )
        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign:jobs-route", name="Кампания jobs"
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset:campaign:jobs-route:1",
            campaign_id=campaign.id,
            name="Адсет jobs",
        )
        ad = await ads_repo.upsert_ad(
            fb_ad_id="action-job-ad-1",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="Объявление jobs",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            risk_band=RiskBand.STOP,
            last_seen_at=datetime(2026, 3, 23, 11, 2, tzinfo=UTC),
        )
        scan_run = await scan_repo.create_scan_run(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status=ScanRunStatus.SUCCEEDED,
            started_at=datetime(2026, 3, 23, 11, 2, tzinfo=UTC),
            pipeline_kind=ScanPipelineKind.FULL_SCAN,
        )
        decision = await decisions_repo.create_decision(
            scan_run_id=scan_run.id,
            fb_ad_id=ad.fb_ad_id,
            decision=DecisionType.WOULD_PAUSE,
            reason="Стоп",
            ad_id=ad.id,
        )
        await jobs_repo.enqueue_action_job(
            decision_id=decision.id,
            ad_id=ad.id,
            fb_ad_id=ad.fb_ad_id,
            profile_id=profile.id,
            browser_host_id=browser_host.id,
            action_type=ActionType.PAUSE,
            priority_score=1200,
            next_attempt_at=datetime(2026, 3, 23, 11, 3, tzinfo=UTC),
        )
        await session.commit()

    response = await client.get("/action-jobs")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["fb_ad_id"] == "action-job-ad-1"
    assert payload[0]["profile_id"] == "vision-action-profile"
    assert payload[0]["browser_host_id"] == "vision-action-jobs-route"
    assert payload[0]["campaign_name"] == "Кампания jobs"
    assert payload[0]["action_type"] == "PAUSE"
    assert payload[0]["status"] == "QUEUED"


# Проверяет, что список проблемных профилей и ручной reset стопа работают через API.
@pytest.mark.asyncio
async def test_suspended_profiles_routes_list_and_reset_profile(
    api_client,
    async_session_factory,
) -> None:
    client, _, _ = api_client

    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        browser_host = await browser_repo.upsert_browser_host(
            name="vision-suspend",
            vendor="vision",
            api_base_url="http://127.0.0.1:3030",
            is_enabled=True,
            last_heartbeat_at=datetime(2026, 3, 22, 10, 0, tzinfo=UTC),
        )
        await browser_repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="vision-profile-suspended",
            display_name="Vision профиль suspended",
            is_active=True,
            last_launch_at=datetime(2026, 3, 22, 10, 1, tzinfo=UTC),
        )
        await browser_repo.suspend_profile_scan(
            "vision-profile-suspended",
            "Не удалось получить полный набор строк Ads Manager после одного обновления страницы",
        )
        await session.commit()

    list_response = await client.get("/settings/suspended-profiles")
    reset_response = await client.post(
        "/settings/suspended-profiles/vision-profile-suspended/reset"
    )
    list_after_reset_response = await client.get("/settings/suspended-profiles")

    assert list_response.status_code == 200
    assert list_response.json()[0]["profile_id"] == "vision-profile-suspended"
    assert list_response.json()[0]["browser_host_id"] == "vision-suspend"
    assert "полный набор строк Ads Manager" in list_response.json()[0]["reason"]
    assert reset_response.status_code == 200
    assert reset_response.json()["message"] == "Сканирование профиля снова разрешено"
    assert reset_response.json()["profile"]["profile_id"] == "vision-profile-suspended"
    assert list_after_reset_response.status_code == 200
    assert list_after_reset_response.json() == []


# Проверяет, что reset проблемного профиля возвращает 404 для неизвестного profile id.
@pytest.mark.asyncio
async def test_suspended_profiles_reset_returns_not_found_for_unknown_profile(api_client) -> None:
    client, _, _ = api_client

    response = await client.post("/settings/suspended-profiles/missing-profile/reset")

    assert response.status_code == 404
    assert response.json()["detail"] == "Профиль `missing-profile` не найден"


# Проверяет, что оффер можно создать одной формой с автоматическим кодом и стартовой ставкой.
@pytest.mark.asyncio
async def test_create_offer_route_generates_code_and_initial_rate(
    api_client,
    async_session_factory,
) -> None:
    client, _, _ = api_client

    response = await client.post(
        "/offers",
        json={
            "name": "Новый оффер CPA",
            "cpa_usd": "7.50",
            "is_active": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["offer"]["name"] == "Новый оффер CPA"
    assert response.json()["offer"]["current_cpa_usd"] == "7.50"
    assert response.json()["offer"]["code"] != ""

    async with async_session_factory() as session:
        offers_repo = OffersRepository(session)
        offer = await offers_repo.get_offer(response.json()["offer"]["id"])
        rate = await offers_repo.resolve_rate_version(
            response.json()["offer"]["id"],
            datetime.now(tz=UTC),
        )

    assert offer is not None
    assert rate is not None
    assert rate.cpa_usd == Decimal("7.50")


# Проверяет, что API удаляет оффер и он исчезает из списка без остатка.
@pytest.mark.asyncio
async def test_delete_offer_route_removes_offer(api_client, async_session_factory) -> None:
    client, _, _ = api_client

    async with async_session_factory() as session:
        offers_repo = OffersRepository(session)
        offer = await offers_repo.create_offer(code="offer-delete-1", name="Удаляемый оффер")
        await offers_repo.add_rate_version(
            offer_id=offer.id,
            cpa_usd=Decimal("6.50"),
            effective_from=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        await session.commit()

    response = await client.delete(f"/offers/{offer.id}")
    list_response = await client.get("/offers")

    assert response.status_code == 200
    assert response.json()["message"] == "Оффер удален"
    assert list_response.status_code == 200
    assert list_response.json() == []

    async with async_session_factory() as session:
        offers_repo = OffersRepository(session)
        deleted_offer = await offers_repo.get_offer(str(offer.id))

    assert deleted_offer is None


# Проверяет, что API показывает CPA оффера, найденного по коду из имени объявления.
@pytest.mark.asyncio
async def test_ads_route_resolves_cpa_from_ad_naming(api_client, async_session_factory) -> None:
    client, _, _ = api_client

    async with async_session_factory() as session:
        ads_repo = AdsRepository(session)
        offers_repo = OffersRepository(session)

        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign:auto-account:auto-campaign",
            name="Авто кампания",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset:campaign:auto-account:auto-campaign:auto-adset",
            campaign_id=campaign.id,
            name="Авто адсет",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 10, 1, tzinfo=UTC),
        )
        await ads_repo.upsert_ad(
            fb_ad_id="auto-ad-1",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="DRC_CR2_CR001",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 20, 10, 2, tzinfo=UTC),
        )
        offer = await offers_repo.create_offer(code="offer-auto-cpa-1", name="DRC_CR2")
        await offers_repo.add_rate_version(
            offer_id=offer.id,
            cpa_usd=Decimal("9.50"),
            effective_from=datetime(2026, 3, 20, 9, 0, tzinfo=UTC),
        )
        await session.commit()

    response = await client.get("/ads")

    assert response.status_code == 200
    auto_ad = next(item for item in response.json() if item["fb_ad_id"] == "auto-ad-1")
    assert auto_ad["resolved_cpa_usd"] == "9.50"


# Проверяет, что API возвращает созданные привязки офферов вместе с кодом оффера.
@pytest.mark.asyncio
async def test_offer_bindings_route_reads_database(api_client, async_session_factory) -> None:
    client, _, _ = api_client

    async with async_session_factory() as session:
        offers_repo = OffersRepository(session)
        offer = await offers_repo.create_offer(code="offer-bind-1", name="Оффер для привязки")
        await offers_repo.upsert_binding(
            EntityType.ADSET,
            "adset:campaign:account-1:campaign-1:adset-99",
            offer.id,
            priority=7,
            is_active=True,
        )
        await session.commit()

    response = await client.get("/offer-bindings")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": response.json()[0]["id"],
            "entity_type": "adset",
            "entity_id": "adset:campaign:account-1:campaign-1:adset-99",
            "offer_id": response.json()[0]["offer_id"],
            "offer_code": "offer-bind-1",
            "priority": 7,
            "is_active": True,
            "created_at": response.json()[0]["created_at"],
            "updated_at": response.json()[0]["updated_at"],
        }
    ]


# Проверяет, что запуск и остановка browser session проходят через базу и реальный session endpoint API.
@pytest.mark.asyncio
async def test_session_routes_persist_browser_sessions(api_client) -> None:
    client, fake_adapter, _ = api_client

    start_response = await client.post(
        "/sessions/vision-profile-1/start",
        json={
            "browser_host_id": "browser-host-local",
            "reason": "Тестовый запуск",
        },
    )
    list_response = await client.get("/sessions")
    stop_response = await client.post(
        "/sessions/vision-profile-1/stop",
        json={
            "browser_host_id": "browser-host-local",
            "reason": "Тестовая остановка",
        },
    )
    session_response = await client.get("/sessions/vision-profile-1")

    assert start_response.status_code == 200
    assert start_response.json()["session"]["status"] == "ACTIVE"
    assert start_response.json()["session"]["cdp_url"] == "http://127.0.0.1:54000"
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert stop_response.status_code == 200
    assert stop_response.json()["session"]["status"] == "STOPPED"
    assert session_response.json()["status"] == "STOPPED"
    assert fake_adapter.started_profiles == ["vision-profile-1"]
    assert fake_adapter.stopped_profiles.count("vision-profile-1") == 1
