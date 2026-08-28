# -*- coding: utf-8 -*-
"""Загрузчик настроенности канала Vision: отсутствие строки — не отказ."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from core.vision import channel_config
from core.vision.channel_config import load_vision_channel_configuration


class _FakeResult:
    def __init__(self, row) -> None:
        self._row = row

    def one_or_none(self):
        return self._row


class _FakeSession:
    """Минимальный двойник сессии: возвращает заранее заданную строку."""

    def __init__(self, row) -> None:
        self._row = row

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def execute(self, *_args, **_kwargs) -> _FakeResult:
        return _FakeResult(self._row)


def _with_row(monkeypatch, row) -> None:
    monkeypatch.setattr(channel_config, "AsyncSession", lambda _engine: _FakeSession(row))


@pytest.mark.asyncio
async def test_missing_configuration_row_is_not_configured(monkeypatch) -> None:
    """Чистый хост: строки настроек ещё нет — это «не настроено», а не исключение."""
    _with_row(monkeypatch, None)

    configuration = await load_vision_channel_configuration(object())

    assert configuration.has_token is False
    assert configuration.profile_id == ""
    assert configuration.is_configured is False


@pytest.mark.asyncio
async def test_blank_token_counts_as_missing(monkeypatch) -> None:
    """Пустое или пробельное значение токена — это отсутствие токена."""
    _with_row(monkeypatch, SimpleNamespace(x_token_encrypted="   ", profile_id="profile-1"))

    configuration = await load_vision_channel_configuration(object())

    assert configuration.has_token is False
    assert configuration.profile_id == "profile-1"
    assert configuration.is_configured is False


@pytest.mark.asyncio
async def test_token_without_profile_is_not_configured(monkeypatch) -> None:
    """Токен введён, профиль не выбран — настройка не закончена."""
    _with_row(monkeypatch, SimpleNamespace(x_token_encrypted="cipher", profile_id=None))

    configuration = await load_vision_channel_configuration(object())

    assert configuration.has_token is True
    assert configuration.profile_id == ""
    assert configuration.is_configured is False


@pytest.mark.asyncio
async def test_full_configuration_is_normalized(monkeypatch) -> None:
    """Оба значения заданы: профиль отдаётся без окружающих пробелов."""
    _with_row(monkeypatch, SimpleNamespace(x_token_encrypted="cipher", profile_id="  profile-1 "))

    configuration = await load_vision_channel_configuration(object())

    assert configuration.has_token is True
    assert configuration.profile_id == "profile-1"
    assert configuration.is_configured is True


def test_token_is_never_decrypted() -> None:
    """Расшифровка токена здесь не нужна: читается только факт наличия значения."""
    source = inspect.getsource(channel_config)

    assert "decrypt" not in source
