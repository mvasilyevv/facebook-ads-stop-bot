# -*- coding: utf-8 -*-
"""Интеграционные тесты: роутеры /tools (creative-uniquify + campaign-create).

Все тесты используют fakeredis и мокируют core-функции:
- uniquify_creatives через monkeypatch (без реального PIL)
- open_generated_folder через monkeypatch (без реального subprocess)
- list_creative_folders / inspect_creative_folder через monkeypatch
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import create_app


def _make_app():
    """Собрать FastAPI для тестов (без подмены зависимостей БД/Redis)."""
    return create_app()


# ─────────────────── POST /tools/creative-uniquify ───────────────────────────


# Happy path: маленький PNG + copies=2 → 200 с output_dir и files_created
@pytest.mark.asyncio
async def test_creative_uniquify_happy(monkeypatch) -> None:
    """Успешная уникализация: один файл, copies=2 → 200 + ожидаемые поля."""
    from core.creatives.service import CreativeOutputFile, CreativeUniquifyResult

    mock_result = CreativeUniquifyResult(
        root_dir="/tmp/FB_Agent_Creo",
        iteration_dir="/tmp/FB_Agent_Creo/test_2025-01-01_00-00-00_1creo_2copies",
        iteration_name="test_2025-01-01_00-00-00_1creo_2copies",
        creative_count=1,
        copy_count=2,
        files=[
            CreativeOutputFile(
                copy_index=1,
                source_name="test.png",
                output_name="test_1.jpeg",
                output_path="/tmp/FB_Agent_Creo/test_.../1/test_1.jpeg",
            ),
            CreativeOutputFile(
                copy_index=2,
                source_name="test.png",
                output_name="test_2.jpeg",
                output_path="/tmp/FB_Agent_Creo/test_.../2/test_2.jpeg",
            ),
        ],
    )

    monkeypatch.setattr(
        "apps.api.routers.v1.tools.uniquify_creatives",
        AsyncMock(return_value=mock_result),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify",
            data={"offer_name": "DRC_CR2", "copies": "2"},
            files={"files": ("test.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["files_created"] == 2
    assert "output_dir" in body
    assert body["copy_count"] == 2


# copies=0 → 422 (валидация)
@pytest.mark.asyncio
async def test_creative_uniquify_invalid_copies_zero() -> None:
    """copies=0 должен вернуть 422 без вызова core-функции."""
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify",
            data={"offer_name": "DRC_CR2", "copies": "0"},
            files={"files": ("test.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
        )
    assert resp.status_code == 422


# Нет файлов → 422
@pytest.mark.asyncio
async def test_creative_uniquify_no_files() -> None:
    """Запрос без files → 422."""
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify",
            data={"offer_name": "DRC_CR2", "copies": "2"},
        )
    assert resp.status_code == 422


# ─────────────── POST /tools/creative-uniquify/open-folder ───────────────────


# path за пределами корня → 403
@pytest.mark.asyncio
async def test_open_folder_path_outside_root(monkeypatch) -> None:
    """Попытка открыть путь вне FB_Agent_Creo → 403 от open_generated_folder."""
    from core.creatives.folder_opener import CreativeFolderOpenError

    async def _fake_open(path, *, base_dir=None):
        raise CreativeFolderOpenError("Можно открывать только папки внутри FB_Agent_Creo")

    monkeypatch.setattr("apps.api.routers.v1.tools.open_generated_folder", _fake_open)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify/open-folder",
            json={"path": "/etc/passwd"},
        )
    assert resp.status_code == 403


# Happy path → 200 (subprocess замокан внутри open_generated_folder)
@pytest.mark.asyncio
async def test_open_folder_happy(monkeypatch) -> None:
    """Корректный путь внутри FB_Agent_Creo → 200."""
    monkeypatch.setattr(
        "apps.api.routers.v1.tools.open_generated_folder",
        AsyncMock(return_value=None),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify/open-folder",
            json={"path": "/Users/user/Documents/FB_Agent_Creo/offer_2025"},
        )
    assert resp.status_code == 200


# ─────────────── GET /tools/campaign-create/folders ──────────────────────────


# Пустой корень → []
@pytest.mark.asyncio
async def test_campaign_folders_empty(monkeypatch) -> None:
    """Нет папок в корне → возвращается пустой список."""
    monkeypatch.setattr(
        "apps.api.routers.v1.tools.list_creative_folders",
        AsyncMock(return_value=[]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/tools/campaign-create/folders")

    assert resp.status_code == 200
    assert resp.json() == []


# Папки есть → возвращает files_count / size_bytes
@pytest.mark.asyncio
async def test_campaign_folders_with_data(monkeypatch) -> None:
    """При наличии папок возвращает список с adset_count и creative_count."""
    from core.campaign_scripts.creative_folder import CampaignCreativeFolderSummary

    fake_summaries = [
        CampaignCreativeFolderSummary(
            name="DRC_CR2_2025",
            path="/home/user/Documents/FB_Agent_Creo/DRC_CR2_2025",
            adset_count=3,
            creative_count=5,
            media_type="image",
            updated_at=1700000000.0,
            is_valid=True,
            validation_error="",
        ),
    ]
    monkeypatch.setattr(
        "apps.api.routers.v1.tools.list_creative_folders",
        AsyncMock(return_value=fake_summaries),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/tools/campaign-create/folders")

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["name"] == "DRC_CR2_2025"
    assert items[0]["adset_count"] == 3
    assert items[0]["creative_count"] == 5
    assert items[0]["is_valid"] is True


# ─────────────── POST /tools/campaign-create/plan ────────────────────────────


# Happy path: корректный body → 200 с campaign_name
@pytest.mark.asyncio
async def test_campaign_plan_happy(monkeypatch) -> None:
    """Корректный запрос → 200, возвращает campaign_name и adsets."""
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
            add_locations=["Антарктика", "Congo"],
            offer_country_name="Congo",
            required_location_type="Страна/регион",
            remove_initial_location_after_add=True,
            rejected_location_terms=["город"],
        ),
        manual_guide=[
            CampaignManualGuideSection(
                title="Кампания",
                items=[
                    CampaignManualGuideItem(
                        label="Название",
                        value="MV | DRC | CR2 | adset.pro | 02.01",
                    )
                ],
            )
        ],
        safety_notes=["Не нажимать Опубликовать без разрешения"],
    )

    monkeypatch.setattr(
        "apps.api.routers.v1.tools.inspect_creative_folder",
        AsyncMock(return_value=fake_folder),
    )
    monkeypatch.setattr(
        "apps.api.routers.v1.tools.build_campaign_script_plan",
        MagicMock(return_value=fake_plan),
    )

    app = _make_app()
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

    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_name"] == "MV | DRC | CR2 | adset.pro | 02.01"
    assert body["adset_count"] == 1


# Невалидный body (нет обязательных полей) → 422
@pytest.mark.asyncio
async def test_campaign_plan_invalid_body() -> None:
    """Отсутствие обязательных полей → 422 от Pydantic."""
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/campaign-create/plan",
            json={"copies": 2},  # нет offer_code, offer_country_name, cabinet_id, folder_name
        )
    assert resp.status_code == 422


# Тест что при copies=51 (> MAX_COPY_COUNT) возвращается 422
@pytest.mark.asyncio
async def test_creative_uniquify_copies_too_large() -> None:
    """copies больше 50 → 422."""
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify",
            data={"offer_name": "DRC_CR2", "copies": "51"},
            files={"files": ("test.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
        )
    assert resp.status_code == 422
