# -*- coding: utf-8 -*-
"""Unit-тесты pure-логики core/meta_api/autostop_alert.py.

Сценарий: классификация «канал auto-stop мёртв» vs прочие ошибки + рендер CRITICAL-текста.
"""

from __future__ import annotations

import inspect

import grpc

import core.meta_api.autostop_alert as autostop_alert
from core.meta_api.autostop_alert import (
    is_channel_down_error,
)
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
    AmbiguousResultError,
    NotFoundError,
    PermanentError,
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
    TokenInvalidError,
)


# code=-2 "Failed to fetch" (наш инцидент) — канал мёртв
def test_failed_to_fetch_is_channel_down() -> None:
    exc = TemporaryError("Failed to fetch", code=-2)
    assert is_channel_down_error(exc) is True


# Vision-сессия доказанно недоступна (-1/circuit-open) — канал мёртв.
# Page-evaluate loss (-3) тоже сигнал outage, но исход мутации ambiguous.
def test_session_unavailable_is_channel_down() -> None:
    assert is_channel_down_error(SessionUnavailableError("token_not_found", code=-1)) is True
    assert is_channel_down_error(AmbiguousResultError("page-evaluate", code=-3)) is True


# gRPC UNAVAILABLE → ambiguous с code=None — канал мёртв
def test_grpc_unavailable_is_channel_down() -> None:
    exc = AmbiguousResultError("browser-agent response lost (UNAVAILABLE)", code=None)
    assert is_channel_down_error(exc) is True


# Rate-limit — это Meta-side throttling, КАНАЛ ЖИВ → НЕ алертим как «канал мёртв»
def test_rate_limited_is_not_channel_down() -> None:
    assert is_channel_down_error(RateLimitedError("throttled", code=4)) is False


# Положительный Graph-код (Meta ответила) — канал жив
def test_positive_graph_code_temporary_is_not_channel_down() -> None:
    assert is_channel_down_error(TemporaryError("API service", code=2)) is False


# Permanent-ошибки (токен/нет объекта/нет прав) — не «канал мёртв» (другой алерт-путь)
def test_permanent_errors_are_not_channel_down() -> None:
    assert is_channel_down_error(TokenInvalidError("revoked", code=190)) is False
    assert is_channel_down_error(NotFoundError("gone", code=803)) is False
    assert is_channel_down_error(PermanentError("nope", code=1)) is False


# Реальная gRPC-ошибка канала из MetaApiClient (UNAVAILABLE) классифицируется как канал-мёртв
def test_real_grpc_error_mapped_is_channel_down() -> None:
    class _Rpc(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "browser-agent упал"

    mapped = MetaApiClient._grpc_to_meta_error(_Rpc(), endpoint="/123")
    assert is_channel_down_error(mapped) is True


def test_notification_path_has_no_redis_gate() -> None:
    source = inspect.getsource(autostop_alert)
    assert "redis_client" not in source
    assert "autostop:net_fail_count" not in source
    assert "autostop:alerted" not in source
