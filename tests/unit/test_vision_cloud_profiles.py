"""Список профилей Vision Cloud: живые имена вместо UUID в интерфейсе."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from core.vision.cloud_profiles import list_vision_profiles

FOLDER = "c5683e1a-d5aa-47a5-8aac-8f45e7ff8bed"


def _response(status_code: int, payload: object | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", "https://vision"))


def _items(*items: dict[str, object]) -> dict[str, object]:
    return {"data": {"total": len(items), "items": list(items)}}


@pytest.mark.asyncio
async def test_asks_the_documented_folder_endpoint() -> None:
    """Облако не имеет ручки /list — она принадлежит локальному агенту."""
    client = AsyncMock()
    client.get.return_value = _response(200, _items())

    await list_vision_profiles(
        "https://vision.example/api/v1",
        token="opaque",
        folder_id=FOLDER,
        http_client=client,
    )

    assert client.get.await_args.args[0] == (
        f"https://vision.example/api/v1/folders/{FOLDER}/profiles"
    )
    assert client.get.await_args.kwargs["headers"] == {"X-Token": "opaque"}


@pytest.mark.asyncio
async def test_returns_names_statuses_and_tags() -> None:
    client = AsyncMock()
    client.get.return_value = _response(
        200,
        _items(
            {
                "id": "6a572873-24df-43b0-be1d-98939ac3b2e9",
                "profile_name": "Desk 10 1000091",
                "profile_status": {"status": "Активно", "status_color": "#0f0"},
                "profile_tags": [{"tag": "OBUCH"}],
                "running": True,
                "last_run_at": "2026-08-15T18:00:00Z",
            },
            {
                "id": "3a47ef23-c5bf-4bdb-bbda-275173a6d64d",
                "profile_name": "desk10 2608 2B",
                "profile_status": "BAN",
                "profile_tags": ["OBUCH"],
                "running": False,
                "last_run_at": None,
            },
        ),
    )

    result = await list_vision_profiles(
        "https://vision.example/api/v1",
        token="opaque",
        folder_id=FOLDER,
        http_client=client,
    )

    assert result.state == "ready"
    assert [profile.name for profile in result.items] == ["Desk 10 1000091", "desk10 2608 2B"]
    assert [profile.status for profile in result.items] == ["Активно", "BAN"]
    assert [profile.tags for profile in result.items] == [("OBUCH",), ("OBUCH",)]
    assert [profile.running for profile in result.items] == [True, False]


@pytest.mark.asyncio
async def test_rejected_token_never_leaks_into_the_result() -> None:
    client = AsyncMock()
    client.get.return_value = _response(401, {"message": "secret"})

    result = await list_vision_profiles(
        "https://vision.example/api/v1",
        token="token-that-must-not-be-returned",
        folder_id=FOLDER,
        http_client=client,
    )

    assert result.state == "token_rejected"
    assert result.items == ()
    assert "token-that-must-not-be-returned" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload", "expected"),
    [
        (404, None, "folder_not_found"),
        (500, None, "unavailable"),
        (200, {"data": {"items": "нет"}}, "unavailable"),
        (200, {"нет": "данных"}, "unavailable"),
    ],
)
async def test_unexpected_answers_never_look_like_an_empty_folder(
    status_code: int,
    payload: object | None,
    expected: str,
) -> None:
    """Пустой список и сломанный ответ — разные состояния: одно не заменяет другое."""
    client = AsyncMock()
    client.get.return_value = _response(status_code, payload)

    result = await list_vision_profiles(
        "https://vision.example/api/v1",
        token="opaque",
        folder_id=FOLDER,
        http_client=client,
    )

    assert result.state == expected


@pytest.mark.asyncio
async def test_network_failure_is_unavailable_not_an_exception() -> None:
    client = AsyncMock()
    client.get.side_effect = httpx.ConnectError("нет сети")

    result = await list_vision_profiles(
        "https://vision.example/api/v1",
        token="opaque",
        folder_id=FOLDER,
        http_client=client,
    )

    assert result.state == "unavailable"


@pytest.mark.asyncio
async def test_profile_without_name_keeps_its_identifier_visible() -> None:
    """Безымянный профиль не должен превращаться в пустую строку в списке."""
    client = AsyncMock()
    client.get.return_value = _response(
        200,
        _items({"id": "6a572873-24df-43b0-be1d-98939ac3b2e9", "profile_name": "   "}),
    )

    result = await list_vision_profiles(
        "https://vision.example/api/v1",
        token="opaque",
        folder_id=FOLDER,
        http_client=client,
    )

    assert result.items[0].name == "6a572873-24df-43b0-be1d-98939ac3b2e9"


@pytest.mark.asyncio
async def test_entries_without_an_identifier_are_dropped() -> None:
    client = AsyncMock()
    client.get.return_value = _response(
        200,
        _items({"profile_name": "Без идентификатора"}, {"id": "", "profile_name": "Пустой"}),
    )

    result = await list_vision_profiles(
        "https://vision.example/api/v1",
        token="opaque",
        folder_id=FOLDER,
        http_client=client,
    )

    assert result.state == "ready"
    assert result.items == ()
