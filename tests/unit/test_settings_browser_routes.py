# -*- coding: utf-8 -*-
"""Тесты API-ручек browser-agent в настройках."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


class _FakeGrpcChannel:
    """Минимальный канал для generated gRPC stub-ов в тестах роутера."""

    def __init__(self, response):
        self.response = response
        self.closed = False

    def unary_unary(self, *_args, **_kwargs):
        async def call(_request):
            return self.response

        return call

    def unary_stream(self, *_args, **_kwargs):
        async def call(_request):
            if False:
                yield self.response

        return call

    async def close(self):
        self.closed = True


# Проверяем, что роутер не превращает осмысленную HTTPException в безликую ошибку 500.
@pytest.mark.asyncio
async def test_validate_browser_columns_preserves_http_exception(monkeypatch):
    import grpc

    from apps.api.routers import settings

    async def fake_get_session_id(_browser_stub, _db, *, start_if_missing):
        raise HTTPException(status_code=409, detail="Активная browser-agent сессия не найдена")

    monkeypatch.setattr(settings, "_get_or_start_browser_agent_session_id", fake_get_session_id)
    monkeypatch.setattr(
        grpc.aio,
        "insecure_channel",
        lambda _target: _FakeGrpcChannel(
            SimpleNamespace(valid=True, missing_columns=[], found_columns=[], error_message="")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await settings.validate_browser_columns(start_if_missing=False, db=AsyncMock())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Активная browser-agent сессия не найдена"


# Проверяем, что ручная перепроверка может явно разрешить старт browser-agent сессии.
@pytest.mark.asyncio
async def test_validate_browser_columns_forwards_start_if_missing(monkeypatch):
    import grpc

    from apps.api.routers import settings

    flags: list[bool] = []

    async def fake_get_session_id(_browser_stub, _db, *, start_if_missing):
        flags.append(start_if_missing)
        return "session-1"

    monkeypatch.setattr(settings, "_get_or_start_browser_agent_session_id", fake_get_session_id)
    monkeypatch.setattr(
        grpc.aio,
        "insecure_channel",
        lambda _target: _FakeGrpcChannel(
            SimpleNamespace(valid=True, missing_columns=[], found_columns=[], error_message="")
        ),
    )

    response = await settings.validate_browser_columns(start_if_missing=True, db=AsyncMock())

    assert flags == [True]
    assert response["valid"] is True
