# -*- coding: utf-8 -*-
"""GET /settings/vision/profiles — живой список профилей вместо ручного UUID.

Список читается из облака на каждый запрос. Ключевое требование: если профиль
в облаке пересоздали и у него сменился идентификатор, оператор узнаёт об этом
при первом же открытии настроек, а не молча продолжает целиться в исчезнувший
кабинет.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import apps.api.routers.v1.settings_vision as m
from core.vision.cloud_profiles import VisionCloudProfile, VisionCloudProfiles

SELECTED = "6a572873-24df-43b0-be1d-98939ac3b2e9"
OTHER = "3a47ef23-c5bf-4bdb-bbda-275173a6d64d"


def _snapshot(
    *, token: str = "enc-token", folder: str = "enc-folder", profile: str | None = SELECTED
):
    return m._VisionSnapshot(
        x_token_encrypted=token,
        profile_id=profile,
        updated_at=datetime(2026, 8, 15, tzinfo=UTC),
        folder_id_encrypted=folder,
    )


@pytest.fixture
def configured(monkeypatch):
    """Настроенный Vision: токен, папка и выбранный профиль на месте."""

    def install(snapshot=None, profiles: VisionCloudProfiles | None = None):
        snap = _snapshot() if snapshot is None else snapshot

        async def fake_load_config(_session):
            return object()

        monkeypatch.setattr(m, "_load_config", fake_load_config)
        monkeypatch.setattr(m, "_snapshot", lambda _config: snap)

        async def fake_runtime(_engine):
            return SimpleNamespace(
                x_token="opaque", profile_id=snap.profile_id or "", folder_id="folder-1"
            )

        monkeypatch.setattr(m, "load_vision_runtime_config", fake_runtime)

        async def fake_list(_url, *, token, folder_id, http_client=None):
            assert token == "opaque"
            assert folder_id == "folder-1"
            return profiles or VisionCloudProfiles("ready")

        monkeypatch.setattr(m, "list_vision_profiles", fake_list)

    return install


def _settings():
    return SimpleNamespace(vision_cloud_url="https://vision.example/api/v1")


@pytest.mark.asyncio
async def test_returns_names_statuses_and_tags(configured, monkeypatch):
    configured(
        profiles=VisionCloudProfiles(
            "ready",
            (
                VisionCloudProfile(
                    SELECTED, "Desk 10 1000091", "Активно", ("OBUCH",), True, "2026-08-15T18:00:00Z"
                ),
                VisionCloudProfile(OTHER, "desk10 2608 2B", "BAN", ("OBUCH",)),
            ),
        )
    )
    monkeypatch.setattr(m, "AsyncSession", _FakeSession)

    response = await m.get_vision_profiles(object(), _settings())

    assert response.state == "ready"
    assert [item.name for item in response.items] == ["Desk 10 1000091", "desk10 2608 2B"]
    assert [item.status for item in response.items] == ["Активно", "BAN"]
    assert response.selected_profile_id == SELECTED
    assert response.selected_present is True
    assert response.message == ""


@pytest.mark.asyncio
async def test_says_it_plainly_when_the_configured_profile_disappeared(configured, monkeypatch):
    """Смена идентификатора в облаке обязана быть видна, а не проглочена."""
    configured(
        profiles=VisionCloudProfiles(
            "ready",
            (VisionCloudProfile(OTHER, "desk10 2608 2B"),),
        )
    )
    monkeypatch.setattr(m, "AsyncSession", _FakeSession)

    response = await m.get_vision_profiles(object(), _settings())

    assert response.state == "ready"
    assert response.selected_present is False
    assert "исчез из облака" in response.message
    # Подменять исчезнувший профиль соседним нельзя: это чужой кабинет.
    assert response.selected_profile_id == SELECTED
    assert [item.id for item in response.items] == [OTHER]


@pytest.mark.asyncio
async def test_empty_folder_is_not_an_error(configured, monkeypatch):
    configured(profiles=VisionCloudProfiles("ready", ()))
    monkeypatch.setattr(m, "AsyncSession", _FakeSession)

    response = await m.get_vision_profiles(object(), _settings())

    assert response.state == "empty"
    assert response.reason == "EMPTY"
    assert response.items == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cloud_state", "expected_reason"),
    [
        ("token_rejected", "TOKEN_REJECTED"),
        ("folder_not_found", "FOLDER_NOT_FOUND"),
        ("unavailable", "CLOUD_UNAVAILABLE"),
    ],
)
async def test_cloud_problems_never_look_like_an_empty_folder(
    configured, monkeypatch, cloud_state: str, expected_reason: str
):
    configured(profiles=VisionCloudProfiles(cloud_state))  # type: ignore[arg-type]
    monkeypatch.setattr(m, "AsyncSession", _FakeSession)

    response = await m.get_vision_profiles(object(), _settings())

    assert response.state == "unavailable"
    assert response.reason == expected_reason
    assert response.items == []
    assert response.selected_present is False


@pytest.mark.asyncio
async def test_missing_token_and_folder_are_told_apart(configured, monkeypatch):
    monkeypatch.setattr(m, "AsyncSession", _FakeSession)

    configured(snapshot=_snapshot(token=""))
    assert (await m.get_vision_profiles(object(), _settings())).reason == "TOKEN_MISSING"

    configured(snapshot=_snapshot(folder=""))
    assert (await m.get_vision_profiles(object(), _settings())).reason == "FOLDER_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_listing_failure_does_not_break_the_settings_screen(configured, monkeypatch):
    configured()
    monkeypatch.setattr(m, "AsyncSession", _FakeSession)

    async def exploding(*_args, **_kwargs):
        raise RuntimeError("облако упало")

    monkeypatch.setattr(m, "list_vision_profiles", exploding)

    response = await m.get_vision_profiles(object(), _settings())

    assert response.state == "unavailable"
    assert response.reason == "CLOUD_UNAVAILABLE"


class _FakeSession:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None
