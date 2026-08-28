# -*- coding: utf-8 -*-
"""Ненастроенный канал молчит, настроенный и упавший — по-прежнему кричит.

Инвариант карточки #317: «не настроено» — тихое видимое состояние, а не поток
аварий. Условие тишины смотрит на то, введены ли учётные данные, а не на то,
ответил ли канал: иначе подавление закроет и настоящий отказ.

Контракт, который обязана дать реализация: у `apps.health_watchdog.main` есть
загрузчик конфигурации канала, возвращающий объект с полями `has_token: bool` и
`profile_id: str`. Тест подменяет его заглушкой (`raising=False`, чтобы на
старом коде падать по ассерту, а не по отсутствию атрибута).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.health_watchdog.main import (
    BROWSER_CONTRACT_VERSION,
    META_CHANNEL_INCIDENT_KEY,
    check_meta_api_channel,
)

_CONFIGURATION_LOADER = "load_vision_channel_configuration"
_PROFILE_ID = "vision-profile-1"

_OK_PROBE = {
    "healthy": True,
    "probe_performed": True,
    "probe_ok": True,
    "probe_status_code": 200,
    "probe_detail": "ok",
    "browser_contract_version": BROWSER_CONTRACT_VERSION,
    "vision_profile_id": _PROFILE_ID,
}
_DOWN_PROBE = {
    "healthy": False,
    "probe_performed": True,
    "probe_ok": False,
    "probe_status_code": 0,
    "probe_detail": "probe_network_down",
}


class _CountingMetaClient:
    """Считает пробы: ненастроенный канал не должен идти в браузер вообще."""

    def __init__(self, responses: list[dict] | None = None) -> None:
        self.responses = responses or [_OK_PROBE]
        self.calls = 0

    async def check_health(
        self,
        *,
        full_probe: bool = False,
        expected_profile_id: str = "",
    ) -> dict:
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


class _ExplodingMetaClient:
    """Настроенный канал, который не отвечает: та же ветка кода, что у молчания."""

    def __init__(self) -> None:
        self.calls = 0

    async def check_health(
        self,
        *,
        full_probe: bool = False,
        expected_profile_id: str = "",
    ) -> dict:
        self.calls += 1
        raise RuntimeError("gRPC boom")


class _NoopBrowserFence:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self) -> _NoopBrowserFence:
        return self

    async def assert_held(self) -> None:
        pass

    async def __aexit__(self, *_args) -> bool:
        return False


@pytest.fixture(autouse=True)
def _channel_environment(monkeypatch):
    """Скан включён, fence безобиден — в фокусе только настроенность канала."""
    import apps.health_watchdog.main as watchdog
    from core.observer import queries as observer_queries

    monkeypatch.setattr(
        observer_queries,
        "load_observer_config",
        AsyncMock(return_value={"is_scanning_enabled": True}),
    )
    monkeypatch.setattr(watchdog, "BrowserOperationFence", _NoopBrowserFence)


def _configure(monkeypatch, *, has_token: bool, profile_id: str) -> None:
    import apps.health_watchdog.main as watchdog

    monkeypatch.setattr(
        watchdog,
        _CONFIGURATION_LOADER,
        AsyncMock(return_value=SimpleNamespace(has_token=has_token, profile_id=profile_id)),
        raising=False,
    )
    if profile_id:
        monkeypatch.setattr(
            watchdog,
            "_load_canonical_vision_profile_id",
            AsyncMock(return_value=profile_id),
        )
    else:
        monkeypatch.setattr(
            watchdog,
            "_load_canonical_vision_profile_id",
            AsyncMock(side_effect=RuntimeError("canonical Vision profile is unavailable")),
        )


@pytest.mark.asyncio
async def test_unconfigured_channel_creates_no_incident(monkeypatch) -> None:
    """Ни токена, ни профиля: штатный первый запуск, а не авария."""
    _configure(monkeypatch, has_token=False, profile_id="")
    meta = _CountingMetaClient()
    notify = AsyncMock(return_value=True)

    with patch("apps.health_watchdog.main.notify_recurring_incident", notify):
        alerted = await check_meta_api_channel(meta, engine=MagicMock())

    assert alerted is False
    notify.assert_not_awaited()
    assert meta.calls == 0


@pytest.mark.asyncio
async def test_unconfigured_channel_stays_silent_every_tick(monkeypatch) -> None:
    """Двенадцать тиков в час не должны давать двенадцать одинаковых инцидентов."""
    _configure(monkeypatch, has_token=False, profile_id="")
    meta = _CountingMetaClient()
    notify = AsyncMock(return_value=True)

    with patch("apps.health_watchdog.main.notify_recurring_incident", notify):
        results = [await check_meta_api_channel(meta, engine=MagicMock()) for _ in range(12)]

    assert results == [False] * 12
    assert notify.await_count == 0


@pytest.mark.asyncio
async def test_token_without_profile_stays_silent(monkeypatch) -> None:
    """Токен введён, профиль ещё не выбран — настройка не закончена, не отказ."""
    _configure(monkeypatch, has_token=True, profile_id="")
    meta = _CountingMetaClient()
    notify = AsyncMock(return_value=True)

    with patch("apps.health_watchdog.main.notify_recurring_incident", notify):
        alerted = await check_meta_api_channel(meta, engine=MagicMock())

    assert alerted is False
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_configured_channel_still_alerts_when_probe_explodes(monkeypatch) -> None:
    """Страж: подавление не имеет права стать безусловным."""
    _configure(monkeypatch, has_token=True, profile_id=_PROFILE_ID)
    meta = _ExplodingMetaClient()
    notify = AsyncMock(return_value=True)

    with patch("apps.health_watchdog.main.notify_recurring_incident", notify):
        alerted = await check_meta_api_channel(meta, engine=MagicMock())

    assert alerted is True
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["incident_key"] == META_CHANNEL_INCIDENT_KEY
    assert meta.calls == 1


@pytest.mark.asyncio
async def test_configured_channel_still_alerts_on_down_probe(monkeypatch) -> None:
    """Настроенный канал, ответивший отказом, остаётся аварией."""
    _configure(monkeypatch, has_token=True, profile_id=_PROFILE_ID)
    meta = _CountingMetaClient([_DOWN_PROBE])
    notify = AsyncMock(return_value=True)

    with patch("apps.health_watchdog.main.notify_recurring_incident", notify):
        alerted = await check_meta_api_channel(meta, engine=MagicMock())

    assert alerted is True
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["severity"] == "critical"


@pytest.mark.asyncio
async def test_configured_and_healthy_channel_does_not_alert(monkeypatch) -> None:
    """Настроенный живой канал молчит по своей причине, а не по подавлению."""
    _configure(monkeypatch, has_token=True, profile_id=_PROFILE_ID)
    meta = _CountingMetaClient([_OK_PROBE])
    notify = AsyncMock(return_value=True)
    resolve = AsyncMock(return_value=True)

    with (
        patch("apps.health_watchdog.main.notify_recurring_incident", notify),
        patch("apps.health_watchdog.main.resolve_recurring_incident", resolve),
    ):
        alerted = await check_meta_api_channel(meta, engine=MagicMock())

    assert alerted is False
    notify.assert_not_awaited()
    assert meta.calls == 1
