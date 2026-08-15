# -*- coding: utf-8 -*-
"""«Переподключить Vision» обязана чинить профиль без CDP-порта.

Профиль, запущенный из окна Vision, виден локальному агенту, но CDP-порта у
него нет: browser-agent находит его в /list и подключиться не может. Обычный
reconnect такое состояние не чинит — он умеет переподключаться, а профиль
нужно перезапустить. Интерфейс при этом советует нажать именно эту кнопку, и
она отвечала 503.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import apps.api.routers.v1.settings_vision as m


class _Fence:
    owner = "a" * 32

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def assert_held(self) -> None:
        return None


@pytest.fixture(autouse=True)
def exclusive_lease(monkeypatch):
    monkeypatch.setattr(m, "BrowserExclusiveMaintenance", _Fence)


def _settings():
    return SimpleNamespace(vision_cloud_url="https://vision.example/api/v1")


@pytest.mark.asyncio
async def test_restarts_the_profile_when_plain_reconnect_cannot_help(monkeypatch) -> None:
    recovered: list[str] = []

    async def failing_reconnect(_engine, _settings):
        raise RuntimeError("профиль без CDP")

    async def fake_recover(_engine, _settings, *, maintenance_owner):
        recovered.append(maintenance_owner)

    monkeypatch.setattr(m, "_reconnect_browser", failing_reconnect)
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", fake_recover)

    response = await m.post_vision_reconnect(object(), _settings())

    assert response.status == "reconnected"
    # Перезапуск идёт под той же арендой, что уже взята и слила чужую работу.
    assert recovered == ["a" * 32]


@pytest.mark.asyncio
async def test_healthy_channel_is_not_restarted(monkeypatch) -> None:
    """Живой канал трогать нельзя: перезапуск закроет открытый кабинет."""
    recovered: list[str] = []

    async def working_reconnect(_engine, _settings):
        return None

    async def fake_recover(_engine, _settings, *, maintenance_owner):
        recovered.append(maintenance_owner)

    monkeypatch.setattr(m, "_reconnect_browser", working_reconnect)
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", fake_recover)

    await m.post_vision_reconnect(object(), _settings())

    assert recovered == []


@pytest.mark.asyncio
async def test_failed_recovery_still_reports_a_generic_failure(monkeypatch) -> None:
    """Если и перезапуск не помог — 503 без внутренних деталей наружу."""
    from fastapi import HTTPException

    async def failing_reconnect(_engine, _settings):
        raise RuntimeError("профиль без CDP")

    async def failing_recover(_engine, _settings, *, maintenance_owner):
        raise RuntimeError("browser-agent недоступен: /secret/path")

    monkeypatch.setattr(m, "_reconnect_browser", failing_reconnect)
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", failing_recover)

    with pytest.raises(HTTPException) as error:
        await m.post_vision_reconnect(object(), _settings())

    assert error.value.status_code == 503
    assert "/secret/path" not in str(error.value.detail)
