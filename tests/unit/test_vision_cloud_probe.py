from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from core.vision.cloud_probe import probe_vision_cloud

FOLDER = "c5683e1a-d5aa-47a5-8aac-8f45e7ff8bed"


def _response(status_code: int, payload: object | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", "https://vision"))


def _profiles(*ids: str) -> dict[str, object]:
    items = [{"id": identifier, "profile_name": identifier} for identifier in ids]
    return {"data": {"total": len(items), "items": items}}


@pytest.mark.asyncio
async def test_cloud_probe_rejects_token_without_exposing_response() -> None:
    client = AsyncMock()
    client.get.return_value = _response(401, {"message": "secret response"})

    result = await probe_vision_cloud(
        "https://vision.example/api/v1",
        token="token-that-must-not-be-returned",
        profile_id="profile-1",
        folder_id=FOLDER,
        http_client=client,
    )

    assert result.state == "token_rejected"
    assert "token-that-must-not-be-returned" not in repr(result)
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_cloud_probe_asks_the_documented_endpoints() -> None:
    """Ручки /list у облака нет — она принадлежит локальному агенту.

    Проба, написанная против неё, всегда получала бы 404 и объявляла канал
    недоступным, что бы ни было в облаке на самом деле.
    """
    client = AsyncMock()
    client.get.side_effect = [
        _response(200, {"id": "owner"}),
        _response(200, _profiles("other")),
    ]

    result = await probe_vision_cloud(
        "https://vision.example/api/v1",
        token="opaque-token",
        profile_id="profile-1",
        folder_id=FOLDER,
        http_client=client,
    )

    assert result.state == "profile_not_found"
    assert [call.args[0] for call in client.get.await_args_list] == [
        "https://vision.example/api/v1/users/me",
        f"https://vision.example/api/v1/folders/{FOLDER}/profiles",
    ]


@pytest.mark.asyncio
async def test_cloud_probe_confirms_the_configured_profile() -> None:
    client = AsyncMock()
    client.get.side_effect = [
        _response(200, {"id": "owner"}),
        _response(200, _profiles("other", "profile-1")),
    ]

    result = await probe_vision_cloud(
        "https://vision.example/api/v1",
        token="opaque-token",
        profile_id="profile-1",
        folder_id=FOLDER,
        http_client=client,
    )

    assert result.state == "ready"


@pytest.mark.asyncio
async def test_cloud_probe_without_folder_cannot_claim_a_missing_profile() -> None:
    """Без папки список недостижим, и это не то же самое, что «профиля нет»."""
    client = AsyncMock()
    client.get.return_value = _response(200, {"id": "owner"})

    result = await probe_vision_cloud(
        "https://vision.example/api/v1",
        token="opaque-token",
        profile_id="profile-1",
        folder_id="",
        http_client=client,
    )

    assert result.state == "unavailable"
