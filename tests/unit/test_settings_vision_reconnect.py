from types import SimpleNamespace

import pytest

from apps.api.routers.v1.schemas.settings_vision import (
    VisionSettingsResponse,
    VisionSettingsUpdateRequest,
)


@pytest.mark.asyncio
async def test_reconnect_passes_folder_id_for_stopped_profile(monkeypatch) -> None:
    import apps.api.routers.v1.settings_vision as module

    async def fake_load_runtime(engine):
        return SimpleNamespace(
            x_token="db-token",
            profile_id="db-profile",
            folder_id="db-folder",
        )

    captured = {}

    class FakeBrowserAgentClient:
        def __init__(self, config):
            captured["config"] = config

        async def start(self):
            return None

        async def reconnect_browser(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(module, "load_vision_runtime_config", fake_load_runtime)
    monkeypatch.setattr(module, "BrowserAgentClient", FakeBrowserAgentClient)
    monkeypatch.setenv("VISION_FOLDER_ID", "must-not-be-used")
    monkeypatch.setenv("BROWSER_AGENT_HOST", "vision-webtop")

    settings = SimpleNamespace(vision_api_url="http://127.0.0.1:3030")
    await module._reconnect_browser(object(), settings)

    config = captured["config"]
    assert config.vision_x_token == "db-token"
    assert config.vision_profile_id == "db-profile"
    assert config.vision_folder_id == "db-folder"
    assert config.grpc_host == "vision-webtop"


@pytest.mark.asyncio
async def test_maintenance_recovery_passes_proven_owner(monkeypatch) -> None:
    import apps.api.routers.v1.settings_vision as module

    async def fake_load_runtime(engine):
        return SimpleNamespace(
            x_token="db-token",
            profile_id="db-profile",
            folder_id="db-folder",
        )

    captured = {}

    class FakeBrowserAgentClient:
        def __init__(self, config):
            captured["config"] = config

        async def start(self):
            return None

        async def recover_browser_profile_under_maintenance(self, *, maintenance_owner):
            captured["maintenance_owner"] = maintenance_owner

        async def close(self):
            return None

    monkeypatch.setattr(module, "load_vision_runtime_config", fake_load_runtime)
    monkeypatch.setattr(module, "BrowserAgentClient", FakeBrowserAgentClient)

    settings = SimpleNamespace(vision_api_url="http://127.0.0.1:3030")
    await module._recover_browser_profile_under_maintenance(
        object(),
        settings,
        maintenance_owner="a" * 32,
    )

    assert captured["maintenance_owner"] == "a" * 32
    assert captured["config"].vision_profile_id == "db-profile"


def test_vision_cloud_secrets_are_hidden_by_request_model() -> None:
    request = VisionSettingsUpdateRequest(
        username="secret-user",
        password="secret-password",
        team_id="secret-team",
        folder_id="secret-folder",
    )

    visible = repr(request)
    assert "secret-user" not in visible
    assert "secret-password" not in visible
    assert "secret-team" not in visible
    assert "secret-folder" not in visible


def test_vision_cloud_fields_reach_update_request_and_response_has_no_password() -> None:
    request = VisionSettingsUpdateRequest(
        username="cloud-user",
        password="cloud-password",
        team_id="team-1",
        folder_id="folder-1",
    )

    assert request.username is not None
    assert request.username.get_secret_value() == "cloud-user"
    assert request.password is not None
    assert request.password.get_secret_value() == "cloud-password"
    assert request.team_id is not None
    assert request.team_id.get_secret_value() == "team-1"
    assert request.folder_id is not None
    assert request.folder_id.get_secret_value() == "folder-1"

    response_payload = VisionSettingsResponse(required_browser_contract_version=5).model_dump()
    assert "password" not in response_payload
    assert "cloud-password" not in str(response_payload)
