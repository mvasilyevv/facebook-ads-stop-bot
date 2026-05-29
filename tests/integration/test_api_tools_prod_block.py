# -*- coding: utf-8 -*-
"""Интеграционные тесты: prod-блокировка /tools/* endpoints.

Проверяет, что при dev_tools_enabled=False все 4 dev-only endpoints
возвращают 403, а при dev_tools_enabled=True — доступны (не 403).
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.deps import get_settings
from apps.api.main import create_app
from core.config import Settings


def _make_app(dev_tools_enabled: bool):
    """Собрать FastAPI с нужным значением dev_tools_enabled."""
    app = create_app()
    settings = Settings(dev_tools_enabled=dev_tools_enabled)
    app.dependency_overrides[get_settings] = lambda: settings
    return app


# ──────────────────────── 403 при dev_tools_enabled=False ────────────────────


# POST /tools/creative-uniquify без флага → 403
@pytest.mark.asyncio
async def test_creative_uniquify_blocked_in_prod() -> None:
    """creative-uniquify недоступен в проде: 403 без DEV_TOOLS_ENABLED."""
    app = _make_app(dev_tools_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify",
            data={"offer_name": "DRC_CR2", "copies": "2"},
            files={"files": ("test.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
        )
    assert resp.status_code == 403
    assert "DEV_TOOLS_ENABLED" in resp.json()["detail"]


# POST /tools/creative-uniquify/open-folder без флага → 403
@pytest.mark.asyncio
async def test_open_folder_blocked_in_prod() -> None:
    """open-folder недоступен в проде: 403 без DEV_TOOLS_ENABLED."""
    app = _make_app(dev_tools_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify/open-folder",
            json={"path": "/Users/user/Documents/FB_Agent_Creo/offer"},
        )
    assert resp.status_code == 403
    assert "DEV_TOOLS_ENABLED" in resp.json()["detail"]


# GET /tools/campaign-create/folders без флага → 403
@pytest.mark.asyncio
async def test_campaign_folders_blocked_in_prod() -> None:
    """campaign-create/folders недоступен в проде: 403 без DEV_TOOLS_ENABLED."""
    app = _make_app(dev_tools_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/tools/campaign-create/folders")
    assert resp.status_code == 403
    assert "DEV_TOOLS_ENABLED" in resp.json()["detail"]


# POST /tools/campaign-create/plan без флага → 403
@pytest.mark.asyncio
async def test_campaign_plan_blocked_in_prod() -> None:
    """campaign-create/plan недоступен в проде: 403 без DEV_TOOLS_ENABLED."""
    app = _make_app(dev_tools_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/campaign-create/plan",
            json={
                "offer_code": "DRC_CR2",
                "offer_country_name": "Congo",
                "cabinet_id": "act_123",
                "folder_name": "DRC_CR2_2025",
            },
        )
    assert resp.status_code == 403
    assert "DEV_TOOLS_ENABLED" in resp.json()["detail"]


# ──────────────────────── Доступность при dev_tools_enabled=True ─────────────


# POST /tools/creative-uniquify с флагом → не 403 (happy-path)
@pytest.mark.asyncio
async def test_creative_uniquify_accessible_with_flag(monkeypatch) -> None:
    """creative-uniquify доступен при DEV_TOOLS_ENABLED=true: статус не 403."""
    from core.creatives.service import CreativeOutputFile, CreativeUniquifyResult

    mock_result = CreativeUniquifyResult(
        root_dir="/tmp/FB_Agent_Creo",
        iteration_dir="/tmp/FB_Agent_Creo/test_2025-01-01_1creo_2copies",
        iteration_name="test_2025-01-01_1creo_2copies",
        creative_count=1,
        copy_count=2,
        files=[
            CreativeOutputFile(
                copy_index=1,
                source_name="test.png",
                output_name="test_1.jpeg",
                output_path="/tmp/FB_Agent_Creo/test_/test_1.jpeg",
            ),
        ],
    )
    monkeypatch.setattr(
        "apps.api.routers.v1.tools.uniquify_creatives",
        AsyncMock(return_value=mock_result),
    )

    app = _make_app(dev_tools_enabled=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify",
            data={"offer_name": "DRC_CR2", "copies": "2"},
            files={"files": ("test.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
        )
    assert resp.status_code != 403


# POST /tools/creative-uniquify/open-folder с флагом → не 403
@pytest.mark.asyncio
async def test_open_folder_accessible_with_flag(monkeypatch) -> None:
    """open-folder доступен при DEV_TOOLS_ENABLED=true: статус не 403."""
    monkeypatch.setattr(
        "apps.api.routers.v1.tools.open_generated_folder",
        AsyncMock(return_value=None),
    )

    app = _make_app(dev_tools_enabled=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify/open-folder",
            json={"path": "/Users/user/Documents/FB_Agent_Creo/offer"},
        )
    assert resp.status_code != 403


# GET /tools/campaign-create/folders с флагом → не 403
@pytest.mark.asyncio
async def test_campaign_folders_accessible_with_flag(monkeypatch) -> None:
    """campaign-create/folders доступен при DEV_TOOLS_ENABLED=true: статус не 403."""
    monkeypatch.setattr(
        "apps.api.routers.v1.tools.list_creative_folders",
        AsyncMock(return_value=[]),
    )

    app = _make_app(dev_tools_enabled=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/tools/campaign-create/folders")
    assert resp.status_code != 403


# POST /tools/campaign-create/plan с флагом → не 403
@pytest.mark.asyncio
async def test_campaign_plan_accessible_with_flag(monkeypatch) -> None:
    """campaign-create/plan доступен при DEV_TOOLS_ENABLED=true: статус не 403."""
    from core.campaign_scripts.creative_folder import (
        CampaignCreativeAdSet,
        CampaignCreativeFile,
        CampaignCreativeFolder,
    )
    from core.campaign_scripts.planner import (
        CampaignAdPlan,
        CampaignAdSetPlan,
        CampaignLocationPlan,
        CampaignManualGuideItem,
        CampaignManualGuideSection,
        CampaignScriptPlan,
    )

    fake_folder = CampaignCreativeFolder(
        name="DRC_CR2_2025",
        path="/home/user/Documents/FB_Agent_Creo/DRC_CR2_2025",
        media_type="image",
        adsets=[
            CampaignCreativeAdSet(
                index=1,
                name="1",
                folder_path="/home/user/Documents/FB_Agent_Creo/DRC_CR2_2025/1",
                files=[
                    CampaignCreativeFile(
                        adset_index=1,
                        ad_name="Ad_DRC_1",
                        media_file_name="Ad_DRC_1.jpeg",
                        media_search_name="Ad_DRC_1",
                        media_path="/home/user/.../Ad_DRC_1.jpeg",
                        media_type="image",
                    )
                ],
            )
        ],
    )

    fake_plan = CampaignScriptPlan(
        campaign_name="MV | DRC | CR2 | adset.pro | 02.01",
        offer_code="DRC_CR2",
        offer_country_name="Congo",
        creative_folder_name="DRC_CR2_2025",
        creative_folder_path="/home/user/.../DRC_CR2_2025",
        conversion_event="Покупка",
        cabinet_id="act_123",
        sub2="MV",
        media_type="image",
        adset_count=1,
        ad_count=1,
        adsets=[
            CampaignAdSetPlan(
                name="1",
                folder_path="/home/user/.../1",
                ads=[
                    CampaignAdPlan(
                        name="Ad_DRC_1",
                        media_file_name="Ad_DRC_1.jpeg",
                        media_search_name="Ad_DRC_1",
                        media_path="/home/user/.../Ad_DRC_1.jpeg",
                        media_type="image",
                        url_params="sub2=MV&sub3=Ad_DRC_1",
                    )
                ],
            )
        ],
        location_plan=CampaignLocationPlan(
            add_locations=["Congo"],
            offer_country_name="Congo",
            required_location_type="Страна/регион",
            remove_initial_location_after_add=True,
            rejected_location_terms=["город"],
        ),
        manual_guide=[
            CampaignManualGuideSection(
                title="Кампания",
                items=[CampaignManualGuideItem(label="Название", value="MV | DRC | CR2")],
            )
        ],
        safety_notes=[],
    )

    monkeypatch.setattr(
        "apps.api.routers.v1.tools.inspect_creative_folder",
        AsyncMock(return_value=fake_folder),
    )
    monkeypatch.setattr(
        "apps.api.routers.v1.tools.build_campaign_script_plan",
        MagicMock(return_value=fake_plan),
    )

    app = _make_app(dev_tools_enabled=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/campaign-create/plan",
            json={
                "offer_code": "DRC_CR2",
                "offer_country_name": "Congo",
                "cabinet_id": "act_123",
                "folder_name": "DRC_CR2_2025",
            },
        )
    assert resp.status_code != 403
