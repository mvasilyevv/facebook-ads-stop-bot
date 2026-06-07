# -*- coding: utf-8 -*-
"""Интеграционные тесты: Ручка 6 — разделение dev-only / prod-safe в /tools/.

campaign-create/folders и campaign-create/plan вынесены из-под require_dev_tools:
они должны работать без DEV_TOOLS_ENABLED=true (prod-safe).
creative-uniquify и open-folder остались dev-only: возвращают 403 без флага.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import create_app


def _make_app(dev_tools_enabled: bool = False):
    """Создаём FastAPI с конкретным флагом dev_tools_enabled."""

    from apps.api.deps import get_settings
    from core.config import Settings

    settings = Settings(dev_tools_enabled=dev_tools_enabled)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return app


# campaign-create/folders доступен без DEV_TOOLS_ENABLED (prod-safe).
@pytest.mark.asyncio
async def test_campaign_folders_prod_safe_no_flag():
    """GET /tools/campaign-create/folders без DEV_TOOLS — должен вернуть 200."""
    from core.campaign_scripts.creative_folder import CampaignCreativeFolderSummary

    fake_summaries = [
        CampaignCreativeFolderSummary(
            name="TestFolder",
            path="/tmp/TestFolder",
            adset_count=2,
            creative_count=4,
            media_type="image",
            updated_at=1700000000.0,
            is_valid=True,
            validation_error="",
        )
    ]

    with patch(
        "apps.api.routers.v1.tools.list_creative_folders",
        new=AsyncMock(return_value=fake_summaries),
    ):
        app = _make_app(dev_tools_enabled=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get("/api/tools/campaign-create/folders")

    assert resp.status_code == 200, f"Ожидали 200, получили {resp.status_code}: {resp.text}"
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "TestFolder"


# campaign-create/folders работает и с DEV_TOOLS_ENABLED=true тоже.
@pytest.mark.asyncio
async def test_campaign_folders_works_with_dev_flag_too():
    """GET /tools/campaign-create/folders с DEV_TOOLS — тоже возвращает 200."""
    with patch(
        "apps.api.routers.v1.tools.list_creative_folders",
        new=AsyncMock(return_value=[]),
    ):
        app = _make_app(dev_tools_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get("/api/tools/campaign-create/folders")

    assert resp.status_code == 200


# campaign-create/plan доступен без DEV_TOOLS (prod-safe).
@pytest.mark.asyncio
async def test_campaign_plan_prod_safe_no_flag():
    """POST /tools/campaign-create/plan без DEV_TOOLS — должен вернуть 422 (неверные данные),
    но НЕ 403 (не заблокирован dev-guard). 422 ожидаем потому что папка не существует.
    """
    app = _make_app(dev_tools_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            "/api/tools/campaign-create/plan",
            json={
                "folder_name": "nonexistent_folder_xyz",
                "offer_code": "TEST",
                "offer_country_name": "Ghana",
                "cabinet_id": "act_123",
            },
        )

    # 403 = dev-guard заблокировал. Нам нужно что угодно кроме 403.
    assert resp.status_code != 403, (
        "campaign-create/plan заблокирован dev-guard (403) — ожидали prod-safe"
    )
    # Ожидаем 422 (папка не найдена) или 200 — но не 403.


# creative-uniquify ОСТАЁТСЯ под dev-guard → 403 без флага.
@pytest.mark.asyncio
async def test_creative_uniquify_still_dev_only():
    """POST /tools/creative-uniquify без DEV_TOOLS → 403 (dev-only, флаг не снят)."""
    app = _make_app(dev_tools_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify",
            data={"offer_name": "Test", "copies": "1"},
            files={"files": ("test.jpg", b"fake", "image/jpeg")},
        )

    assert resp.status_code == 403, f"Ожидали 403, получили {resp.status_code}"


# open-folder ОСТАЁТСЯ под dev-guard → 403 без флага.
@pytest.mark.asyncio
async def test_open_folder_still_dev_only():
    """POST /tools/creative-uniquify/open-folder без DEV_TOOLS → 403 (dev-only)."""
    app = _make_app(dev_tools_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            "/api/tools/creative-uniquify/open-folder",
            json={"path": "/tmp/test"},
        )

    assert resp.status_code == 403, f"Ожидали 403, получили {resp.status_code}"


# creative-uniquify с DEV_TOOLS → доступен (не 403).
@pytest.mark.asyncio
async def test_creative_uniquify_works_with_dev_flag():
    """POST /tools/creative-uniquify с DEV_TOOLS=true → НЕ 403 (guard снят)."""
    with patch(
        "apps.api.routers.v1.tools.uniquify_creatives",
        new=AsyncMock(
            return_value=type(
                "R",
                (),
                {
                    "iteration_dir": "/tmp/test",
                    "iteration_name": "test-001",
                    "files": ["a.jpg"],
                    "creative_count": 1,
                    "copy_count": 1,
                },
            )()
        ),
    ):
        app = _make_app(dev_tools_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post(
                "/api/tools/creative-uniquify",
                data={"offer_name": "Test", "copies": "1"},
                files={"files": ("test.jpg", b"fake-image-bytes", "image/jpeg")},
            )

    assert resp.status_code != 403, "С DEV_TOOLS guard не должен блокировать"
