# -*- coding: utf-8 -*-
"""Unit: MetaApiClient.check_health(full_probe) — флаг и probe-поля.

Инцидент 2026-06-19: token-only health давал false-positive «healthy» при мёртвом
сетевом канале. full_probe прокидывает флаг в CheckMetaApiHealthRequest и поднимает
probe-поля ответа в dict; для probe — увеличенный gRPC-таймаут.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.python_grpc.v1 import meta_api_pb2
from core.browser.circuit_breaker import CircuitOpenError
from core.meta_api.client import MetaApiClient


def _client_with_response(resp: object) -> tuple[MetaApiClient, MagicMock]:
    """Фейк-клиент: circuit_breaker.call возвращает заранее заданный resp."""
    cb = MagicMock()
    cb.call = AsyncMock(return_value=resp)
    client = MetaApiClient(circuit_breaker=cb)
    client._stub = MagicMock()  # обходим RuntimeError «не запущен»
    return client, cb


# Token-only (дефолт): флаг full_probe=False, probe-поля «not_performed», таймаут 10с.
@pytest.mark.asyncio
async def test_check_health_token_only_default() -> None:
    resp = meta_api_pb2.CheckMetaApiHealthResponse(
        healthy=True,
        current_url="https://adsmanager.facebook.com/",
        token_present=True,
        token_length=200,
        detail="ok",
        probe_performed=False,
        probe_ok=False,
        probe_status_code=0,
        probe_duration_ms=0,
        probe_detail="not_performed",
    )
    client, cb = _client_with_response(resp)

    out = await client.check_health()

    req = cb.call.call_args.args[1]
    assert req.full_probe is False
    assert cb.call.call_args.kwargs["timeout"] == 10.0
    assert out["healthy"] is True
    assert out["probe_performed"] is False
    assert out["probe_detail"] == "not_performed"


# full_probe=True: флаг доходит до request, probe-поля поднимаются, таймаут 15с.
@pytest.mark.asyncio
async def test_check_health_full_probe_success() -> None:
    resp = meta_api_pb2.CheckMetaApiHealthResponse(
        healthy=True,
        current_url="https://adsmanager.facebook.com/",
        token_present=True,
        token_length=200,
        detail="ok",
        probe_performed=True,
        probe_ok=True,
        probe_status_code=200,
        probe_duration_ms=321,
        probe_detail="ok",
    )
    client, cb = _client_with_response(resp)

    out = await client.check_health(full_probe=True)

    req = cb.call.call_args.args[1]
    assert req.full_probe is True
    assert cb.call.call_args.kwargs["timeout"] == 15.0
    assert out["probe_performed"] is True
    assert out["probe_ok"] is True
    assert out["probe_status_code"] == 200
    assert out["probe_duration_ms"] == 321
    assert out["probe_detail"] == "ok"


# full_probe ловит мёртвый сетевой канал: healthy=False, detail=probe_network_down.
@pytest.mark.asyncio
async def test_check_health_full_probe_network_down() -> None:
    resp = meta_api_pb2.CheckMetaApiHealthResponse(
        healthy=False,
        current_url="https://adsmanager.facebook.com/",
        token_present=True,
        token_length=200,
        detail="probe_network_down",
        probe_performed=True,
        probe_ok=False,
        probe_status_code=0,
        probe_duration_ms=0,
        probe_detail="probe_network_down",
    )
    client, _ = _client_with_response(resp)

    out = await client.check_health(full_probe=True)

    assert out["healthy"] is False
    assert out["detail"] == "probe_network_down"
    assert out["probe_detail"] == "probe_network_down"
    assert out["probe_ok"] is False


# CircuitOpenError (browser-agent недоступен) → healthy=False + probe-поля «not_performed».
@pytest.mark.asyncio
async def test_check_health_circuit_open() -> None:
    cb = MagicMock()
    cb.call = AsyncMock(side_effect=CircuitOpenError("meta-api", 60.0))
    client = MetaApiClient(circuit_breaker=cb)
    client._stub = MagicMock()

    out = await client.check_health(full_probe=True)

    assert out["healthy"] is False
    assert "circuit_open" in out["detail"]
    assert out["probe_performed"] is False
    assert out["probe_detail"] == "not_performed"
