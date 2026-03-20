from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from apps.browser_host.adapters.models import AdapterHealth, AutomationLaunchResult, ProfileStatus
from core.domain import DecisionType, DeliveryStatus, ScanRunStatus, ScopePresence, TrackingMode
from core.repositories import AdsRepository, DecisionsRepository, ScanRunsRepository


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

    async def ensure_single_active_profile(self) -> None:
        return None

    async def healthcheck(self) -> AdapterHealth:
        return AdapterHealth(is_healthy=True, message="Vision доступен")


@pytest.fixture
async def api_client(async_session_factory, monkeypatch):
    from apps.api import bootstrap as api_bootstrap
    from apps.api import deps as api_deps
    from apps.api.main import app
    from apps.api.routers import sessions as sessions_router
    from apps.api.services import health as health_service

    fake_adapter = FakeVisionAdapter()
    monkeypatch.setattr(api_deps, "get_session_factory", lambda: async_session_factory)
    monkeypatch.setattr(api_bootstrap, "get_session_factory", lambda: async_session_factory)
    monkeypatch.setattr(sessions_router, "build_adapter", lambda settings: fake_adapter)

    async def fake_check_database_connection() -> bool:
        return True

    monkeypatch.setattr(health_service, "check_database_connection", fake_check_database_connection)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, fake_adapter


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
        await ads_repo.upsert_ad(
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
        await session.commit()


# Проверяет, что operational-роуты читают и обновляют реальные данные из базы вместо demo-state.
@pytest.mark.asyncio
async def test_operational_routes_use_database(api_client, async_session_factory) -> None:
    client, _ = api_client
    await _seed_operational_data(async_session_factory)

    ads_response = await client.get("/ads")
    decisions_response = await client.get("/decisions")
    scan_runs_response = await client.get("/scan-runs")

    assert ads_response.status_code == 200
    assert decisions_response.status_code == 200
    assert scan_runs_response.status_code == 200
    assert ads_response.json()[0]["fb_ad_id"] == "ad-1"
    assert ads_response.json()[0]["campaign_name"] == "Кампания 1"
    assert decisions_response.json()[0]["reason"] == "Тестовое решение"
    assert scan_runs_response.json()[0]["rows_seen"] == 1

    ad_detail_response = await client.get("/ads/ad-1")
    assert ad_detail_response.status_code == 200
    assert ad_detail_response.json()["campaign_scope_key"] == "campaign:account-1:campaign-1"
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
    assert flags_response.status_code == 200
    assert len(flags_response.json()) == 1
    assert unblock_response.status_code == 200
    assert unblock_response.json()["ad"]["tracking_mode"] == "TRACKED"
    assert flags_after_unblock.json() == []


# Проверяет, что системные правила поднимаются из базы на старте и обновляются через API без in-memory слоя.
@pytest.mark.asyncio
async def test_rules_routes_work_through_database(api_client) -> None:
    client, _ = api_client

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


# Проверяет, что запуск и остановка browser session проходят через базу и реальный session endpoint API.
@pytest.mark.asyncio
async def test_session_routes_persist_browser_sessions(api_client) -> None:
    client, fake_adapter = api_client

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
    assert fake_adapter.stopped_profiles.count("vision-profile-1") == 2
