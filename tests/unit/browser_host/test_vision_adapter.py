from __future__ import annotations

from datetime import UTC

import httpx
import pytest

from apps.browser_host.adapters import VisionAdapter, VisionAdapterSettings


def _build_adapter(handler) -> VisionAdapter:
    transport = httpx.MockTransport(handler)
    return VisionAdapter(
        settings=VisionAdapterSettings(
            api_token="vision-token",
            cloud_api_url="https://vision-cloud.test/api/v1",
            local_api_url="http://vision-local.test",
            timeout_seconds=5,
        ),
        transport=transport,
    )


# Проверяет, что адаптер Vision корректно собирает список профилей по папкам и помечает активный профиль.
@pytest.mark.asyncio
async def test_vision_adapter_lists_profiles_with_active_flag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/folders":
            return httpx.Response(200, json={"data": [{"folder_id": "folder-1"}]})
        if request.url.path == "/api/v1/folders/folder-1/profiles":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [
                            {
                                "profile_id": "profile-1",
                                "profile_name": "Профиль 1",
                            }
                        ]
                    }
                },
            )
        if request.url.path == "/list":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "profile_id": "profile-1",
                            "name": "Профиль 1",
                            "port": 53000,
                        }
                    ]
                },
            )
        raise AssertionError(f"Неожиданный запрос: {request.method} {request.url}")

    adapter = _build_adapter(handler)

    profiles = await adapter.list_profiles()

    assert len(profiles) == 1
    assert profiles[0].profile_id == "profile-1"
    assert profiles[0].display_name == "Профиль 1"
    assert profiles[0].is_active is True


# Проверяет, что запуск профиля Vision возвращает CDP endpoint и pid из локального API.
@pytest.mark.asyncio
async def test_vision_adapter_starts_profile_for_cdp_automation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/folders":
            return httpx.Response(200, json={"data": [{"folder_id": "folder-1"}]})
        if request.url.path == "/api/v1/folders/folder-1/profiles":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [
                            {
                                "profile_id": "profile-1",
                                "profile_name": "Профиль 1",
                            }
                        ]
                    }
                },
            )
        if request.url.path == "/start/folder-1/profile-1":
            return httpx.Response(200, json={"data": {"port": 54000, "pid": 4321}})
        raise AssertionError(f"Неожиданный запрос: {request.method} {request.url}")

    adapter = _build_adapter(handler)

    launch = await adapter.start_profile_for_automation("profile-1", "cdp", ["--lang=ru"])

    assert launch.profile_id == "profile-1"
    assert launch.vendor == "vision"
    assert launch.cdp_url == "http://127.0.0.1:54000"
    assert launch.debug_port == 54000
    assert launch.webdriver_url is None
    assert launch.browser_pid == 4321
    assert launch.launched_at.tzinfo == UTC


# Проверяет, что healthcheck возвращает русское описание проблемы, если токен Vision не задан.
@pytest.mark.asyncio
async def test_vision_adapter_healthcheck_reports_missing_token() -> None:
    adapter = VisionAdapter(
        settings=VisionAdapterSettings(
            api_token="",
            cloud_api_url="https://vision-cloud.test/api/v1",
            local_api_url="http://vision-local.test",
            timeout_seconds=5,
        )
    )

    health = await adapter.healthcheck()

    assert health.is_healthy is False
    assert health.message == "Не задан токен Vision API в переменной VISION_API_TOKEN"


# Проверяет, что адаптер не пытается запускаться в неподдерживаемом режиме WebDriver.
@pytest.mark.asyncio
async def test_vision_adapter_rejects_unsupported_launch_mode() -> None:
    adapter = _build_adapter(lambda request: httpx.Response(200, json={"data": []}))

    with pytest.raises(RuntimeError, match="Vision сейчас поддерживается только в режиме CDP"):
        await adapter.start_profile_for_automation("profile-1", "webdriver")
