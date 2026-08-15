from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from core.vision.cloud_probe import probe_vision_cloud


def _response(status_code: int, payload: object | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", "https://vision"))


@pytest.mark.asyncio
async def test_cloud_probe_rejects_token_without_exposing_response() -> None:
    client = AsyncMock()
    client.get.return_value = _response(401, {"message": "secret response"})

    result = await probe_vision_cloud(
        "https://vision.example/api/v1",
        token="token-that-must-not-be-returned",
        profile_id="profile-1",
        http_client=client,
    )

    assert result.state == "token_rejected"
    assert "token-that-must-not-be-returned" not in repr(result)
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_cloud_probe_distinguishes_missing_profile() -> None:
    client = AsyncMock()
    client.get.side_effect = [
        _response(200, {"id": "owner"}),
        _response(200, {"profiles": [{"profile_id": "other"}]}),
    ]

    result = await probe_vision_cloud(
        "https://vision.example/api/v1",
        token="opaque-token",
        profile_id="profile-1",
        http_client=client,
    )

    assert result.state == "profile_not_found"
    assert [call.args[0] for call in client.get.await_args_list] == [
        "https://vision.example/api/v1/users/me",
        "https://vision.example/api/v1/list",
    ]
