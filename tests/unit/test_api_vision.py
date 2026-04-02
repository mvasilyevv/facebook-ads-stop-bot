# -*- coding: utf-8 -*-
"""Тесты Vision API в настройках."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Проверяем что reconnect сразу перезапускает профиль, а не только ставит флаг
@pytest.mark.asyncio
async def test_vision_reconnect_restarts_profile_immediately():
    row = SimpleNamespace(
        x_token_encrypted="encrypted",
        profile_id="profile-1",
        api_url="http://127.0.0.1:3030",
        reconnect_requested=False,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.commit = AsyncMock()

    fake_client = AsyncMock()
    fake_client.resolve_folder_id = AsyncMock(return_value="folder-1")
    fake_client.stop_profile = AsyncMock()
    fake_client.wait_until_profile_stopped = AsyncMock(return_value=True)
    fake_client.start_profile = AsyncMock(return_value=SimpleNamespace(port=9222))
    fake_client.close = AsyncMock()

    with (
        patch("apps.api.routers.vision_telegram.decrypt", return_value="token"),
        patch("apps.api.routers.vision_telegram.VisionClient", return_value=fake_client),
        patch("apps.api.routers.settings._stop_observer_process", new=AsyncMock(return_value=1001)),
        patch(
            "apps.api.routers.settings._start_observer_process", new=AsyncMock(return_value=2002)
        ),
    ):
        from apps.api.main import vision_reconnect

        payload = await vision_reconnect(db=mock_db)

    fake_client.resolve_folder_id.assert_awaited_once_with("profile-1")
    fake_client.stop_profile.assert_awaited_once_with("folder-1", "profile-1")
    fake_client.wait_until_profile_stopped.assert_awaited_once_with("profile-1")
    fake_client.start_profile.assert_awaited_once_with("folder-1", "profile-1")
    fake_client.close.assert_awaited_once()
    mock_db.commit.assert_awaited_once()
    assert row.reconnect_requested is True
    assert payload["ok"] is True
    assert payload["port"] == 9222
    assert payload["old_observer_pid"] == 1001
    assert payload["new_observer_pid"] == 2002


# Проверяем что CDP-ошибка переводится в короткую понятную причину для UI
def test_format_observer_runtime_message_for_cdp_error():
    from core.observer.runtime_status import format_observer_runtime_message

    message = format_observer_runtime_message(
        RuntimeError("Vision не вернул CDP-порт для профиля test-profile")
    )

    assert (
        message == "Vision запустил профиль без CDP-порта. Воркер не может подключиться к браузеру."
    )
