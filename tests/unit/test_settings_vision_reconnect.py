from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_reconnect_passes_folder_id_for_stopped_profile(monkeypatch) -> None:
    import apps.api.routers.v1.settings_vision as module

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def fake_load_config(session):
        return None

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

    monkeypatch.setattr(module, "AsyncSession", lambda engine: FakeSession())
    monkeypatch.setattr(module, "_load_config", fake_load_config)
    monkeypatch.setattr(module, "BrowserAgentClient", FakeBrowserAgentClient)
    monkeypatch.setenv("VISION_FOLDER_ID", "folder-current")
    monkeypatch.setenv("BROWSER_AGENT_HOST", "vision-webtop")

    settings = SimpleNamespace(
        vision_x_token="token",
        vision_profile_id="profile-current",
        vision_api_url="http://127.0.0.1:3030",
    )
    await module._reconnect_browser(object(), settings)

    config = captured["config"]
    assert config.vision_folder_id == "folder-current"
    assert config.grpc_host == "vision-webtop"
