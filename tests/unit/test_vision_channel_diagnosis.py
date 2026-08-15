# -*- coding: utf-8 -*-
"""Проба облака Vision — диагностика, а не пропуск к проверке браузера.

Облачный список профилей отвечает на вопрос «почему сломано». На вопрос
«работает ли канал» отвечает только проба браузера: она идёт в живую сессию и
делает настоящий запрос к Graph. Если поставить облако впереди неё, недоступное
облако объявит канал мёртвым при полностью исправном браузере — и деньги
встанут из-за чужого сервиса.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import apps.api.routers.v1.settings_vision as m


def _snapshot():
    return m._VisionSnapshot(
        x_token_encrypted="enc-token",
        profile_id="profile-1",
        updated_at=datetime(2026, 8, 15, tzinfo=UTC),
        folder_id_encrypted="enc-folder",
    )


@pytest.fixture
def configured(monkeypatch):
    async def fake_runtime(_engine):
        return SimpleNamespace(x_token="opaque", profile_id="profile-1", folder_id="folder-1")

    monkeypatch.setattr(m, "load_vision_runtime_config", fake_runtime)
    return fake_runtime


def _settings():
    return SimpleNamespace(vision_cloud_url="https://vision.example/api/v1")


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud_state", ["unavailable", "token_rejected", "profile_not_found"])
async def test_browser_channel_is_probed_even_when_the_cloud_is_silent(
    configured, monkeypatch, cloud_state: str
) -> None:
    """Недоступное облако не должно прятать исправный браузер."""
    probed: list[str] = []

    async def fake_cloud_probe(*_args, **_kwargs):
        return SimpleNamespace(state=cloud_state)

    async def fake_browser_probe(_engine, _client, *, expected_profile_id, maintenance_owner=""):
        probed.append(expected_profile_id)
        return m._BrowserChannelProbe(
            "READY",
            None,
            5,
            True,
            browser_session_id="session-1",
            live_profile_id="profile-1",
            graph_probe_performed=True,
            graph_probe_ok=True,
        )

    monkeypatch.setattr(m, "probe_vision_cloud", fake_cloud_probe)
    monkeypatch.setattr(m, "_fenced_settings_probe", fake_browser_probe)

    probe, _assessment = await m._diagnose_vision_channel(
        object(), object(), _settings(), _snapshot()
    )

    assert probed == ["profile-1"], "проба браузера не выполнялась"
    assert probe.status == "READY"
    assert probe.graph_probe_ok is True


@pytest.mark.asyncio
async def test_cloud_failure_does_not_erase_a_confirmed_browser_channel(
    configured, monkeypatch
) -> None:
    """Исключение в диагностике облака тем более не отменяет живой канал."""

    async def exploding_cloud_probe(*_args, **_kwargs):
        raise RuntimeError("облако упало")

    async def fake_browser_probe(_engine, _client, *, expected_profile_id, maintenance_owner=""):
        return m._BrowserChannelProbe(
            "READY",
            None,
            5,
            True,
            browser_session_id="session-1",
            live_profile_id="profile-1",
            graph_probe_performed=True,
            graph_probe_ok=True,
        )

    monkeypatch.setattr(m, "probe_vision_cloud", exploding_cloud_probe)
    monkeypatch.setattr(m, "_fenced_settings_probe", fake_browser_probe)

    probe, assessment = await m._diagnose_vision_channel(
        object(), object(), _settings(), _snapshot()
    )

    assert probe.status == "READY"
    assert assessment.status == "READY"


@pytest.mark.asyncio
async def test_unconfigured_vision_is_not_probed_at_all(monkeypatch) -> None:
    """Без токена и профиля пробовать нечего — ни облако, ни браузер."""
    called: list[str] = []

    async def fake_cloud_probe(*_args, **_kwargs):
        called.append("cloud")
        return SimpleNamespace(state="ready")

    async def fake_browser_probe(*_args, **_kwargs):
        called.append("browser")
        return m._BrowserChannelProbe("READY", None, 5, True)

    monkeypatch.setattr(m, "probe_vision_cloud", fake_cloud_probe)
    monkeypatch.setattr(m, "_fenced_settings_probe", fake_browser_probe)

    probe, _assessment = await m._diagnose_vision_channel(object(), object(), _settings(), None)

    assert called == []
    assert probe.status == "UNKNOWN"
