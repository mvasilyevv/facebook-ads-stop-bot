# -*- coding: utf-8 -*-
"""Unit-тесты core.meta_api.errors — маппинг Graph error codes/subcodes."""

from __future__ import annotations

from core.meta_api.errors import (
    NotFoundError,
    PermanentError,
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
    TokenInvalidError,
    classify_graph_error,
)
from core.meta_api.errors import (
    PermissionError as MetaPermissionError,
)


# Code 190 — токен сессии invalidated → TokenInvalidError (постоянная, требует re-login).
def test_classify_190_token_invalid() -> None:
    exc = classify_graph_error(190, None, "Session expired")
    assert isinstance(exc, TokenInvalidError)
    assert exc.code == 190


# Code 17 — user request limit reached → RateLimitedError (временная, нужно подождать).
def test_classify_17_rate_limit() -> None:
    exc = classify_graph_error(17, None, "User request limit reached")
    assert isinstance(exc, RateLimitedError)
    assert isinstance(exc, TemporaryError)


# Subcode 33 (100/33) важнее code 100 — это NotFoundError.
def test_subcode_33_overrides_code_100() -> None:
    exc = classify_graph_error(100, 33, "Object does not exist")
    assert isinstance(exc, NotFoundError)


# Subcode 1357045 — re-auth required → TokenInvalidError.
def test_subcode_1357045_reauth() -> None:
    exc = classify_graph_error(190, 1357045, "Login required")
    assert isinstance(exc, TokenInvalidError)


# Code 200 — нет прав на действие → PermissionError (постоянная).
def test_code_200_permission() -> None:
    exc = classify_graph_error(200, None, "Permissions error")
    assert isinstance(exc, MetaPermissionError)
    assert isinstance(exc, PermanentError)


# Code 803 — object id not found → NotFoundError.
def test_code_803_not_found() -> None:
    exc = classify_graph_error(803, None, "Object not found")
    assert isinstance(exc, NotFoundError)


# Неизвестный code + сообщение → PermanentError (не делаем retry на неизвестное).
def test_unknown_code_permanent() -> None:
    exc = classify_graph_error(99999, None, "Some weird thing")
    assert isinstance(exc, PermanentError)


# Сеть/неизвестно (code=None) → TemporaryError (retry разумен).
def test_no_code_temporary() -> None:
    exc = classify_graph_error(None, None, "Network blip")
    assert isinstance(exc, TemporaryError)


# REGRESSION (money): code -2 NetworkError «Failed to fetch» — транзиентный сетевой блип
# Vision-fetch, ДОЛЖЕН быть Temporary (retry), а не Permanent. Иначе авто-стоп pause_ad
# навсегда failed с 1-й попытки при любом блипе сети (кейс CR009 s2).
def test_code_minus2_network_temporary() -> None:
    exc = classify_graph_error(-2, None, "Failed to fetch")
    assert isinstance(exc, TemporaryError)
    assert not isinstance(exc, PermanentError)


# code -3 page-evaluate — переходное состояние page/сессии → Temporary (SessionUnavailable).
def test_code_minus3_page_evaluate_temporary() -> None:
    exc = classify_graph_error(-3, None, "page.evaluate failed")
    assert isinstance(exc, SessionUnavailableError)
    assert isinstance(exc, TemporaryError)


# code -1 TokenNotFound → SessionUnavailableError (Temporary) — токен ещё не в DOM.
def test_code_minus1_token_not_found_temporary() -> None:
    exc = classify_graph_error(-1, None, "EAA-токен не найден")
    assert isinstance(exc, SessionUnavailableError)
    assert isinstance(exc, TemporaryError)


# endpoint/fbtrace_id пробрасываются в exception для дебага.
def test_endpoint_and_fbtrace_propagated() -> None:
    exc = classify_graph_error(
        17,
        None,
        "rate limited",
        endpoint="/act_123/ads",
        fbtrace_id="A1B2C3",
    )
    assert exc.endpoint == "/act_123/ads"
    assert exc.fbtrace_id == "A1B2C3"
