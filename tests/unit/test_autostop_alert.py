# -*- coding: utf-8 -*-
"""Unit-тесты pure-логики core/meta_api/autostop_alert.py.

Сценарий: классификация «канал auto-stop мёртв» vs прочие ошибки + рендер CRITICAL-текста.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import grpc

from core.meta_api.autostop_alert import (
    _minutes_since,
    build_autostop_channel_down_alert,
    build_undelivered_pause_alert,
    is_channel_down_error,
)
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
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


# Vision-сессия недоступна (-1/-3 и circuit-open) — канал мёртв
def test_session_unavailable_is_channel_down() -> None:
    assert is_channel_down_error(SessionUnavailableError("token_not_found", code=-1)) is True
    assert is_channel_down_error(SessionUnavailableError("page-evaluate", code=-3)) is True


# gRPC UNAVAILABLE → TemporaryError с code=None — канал мёртв
def test_grpc_unavailable_is_channel_down() -> None:
    exc = TemporaryError("browser-agent временно недоступен (UNAVAILABLE)", code=None)
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


# CRITICAL-текст содержит ad_id, число фейлов и явный money-сигнал
def test_alert_text_has_money_signal_and_context() -> None:
    text = build_autostop_channel_down_alert(
        fail_count=5,
        fb_ad_id="120246662749510044",
        last_error="Failed to fetch",
    )
    assert "120246662749510044" in text
    assert "5" in text
    # money-сигнал + указание чинить канал, а не «нажми кнопку»
    low = text.lower()
    assert "авто-стоп" in low or "auto-stop" in low
    assert "vision" in low or "graph" in low


# Per-ad алерт «выключи вручную» содержит имя объявления, id, спенд, минуты и «вручную»
def test_undelivered_alert_has_ad_name_spend_and_manual_hint() -> None:
    text = build_undelivered_pause_alert(
        ad_name="GH_CR2_Aviator_001",
        fb_ad_id="120246662749510044",
        spend="123.45",
        minutes_stuck=12,
        last_error="Failed to fetch",
    )
    assert "GH_CR2_Aviator_001" in text
    assert "120246662749510044" in text
    assert "123.45" in text
    assert "12" in text
    assert "вручную" in text.lower()


# Пустые имя/спенд не валят рендер (прочерк), id и минуты на месте
def test_undelivered_alert_handles_missing_name_and_spend() -> None:
    text = build_undelivered_pause_alert(
        ad_name=None,
        fb_ad_id="999",
        spend=None,
        minutes_stuck=20,
        last_error=None,
    )
    assert "999" in text
    assert "—" in text  # прочерк вместо имени/спенда


# _minutes_since: целые минуты от tz-aware метки; None/битое → 0
def test_minutes_since() -> None:
    assert _minutes_since(None) == 0
    past = datetime.now(timezone.utc) - timedelta(minutes=15, seconds=30)
    assert _minutes_since(past) == 15  # дробь усекается вниз
