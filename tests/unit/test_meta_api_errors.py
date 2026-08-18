# -*- coding: utf-8 -*-
"""Unit-тесты core.meta_api.errors — маппинг Graph error codes/subcodes."""

from __future__ import annotations

from core.meta_api.errors import (
    AmbiguousResultError,
    LoginRequiredError,
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


# Code 190 с явным текстом разлогина → отдельный LoginRequiredError даже без subcode.
def test_classify_190_login_message_without_subcode() -> None:
    exc = classify_graph_error(190, None, "Session expired")
    assert isinstance(exc, LoginRequiredError)
    assert exc.code == 190


def test_classify_190_explicit_log_in_message_without_subcode() -> None:
    exc = classify_graph_error(190, None, "The session has been invalidated, please log in")
    assert isinstance(exc, LoginRequiredError)


# Реальный текст Meta 18.08.2026: канал ослеп на 4.5 часа, а классификатор считал
# это рядовым протуханием токена — ни слова «expired», ни «log in», ни subcode.
# Разница не косметическая: ре-логин требует человека у Vision, re-sniff не поможет.
def test_classify_190_session_invalidated_by_password_change() -> None:
    exc = classify_graph_error(
        190,
        None,
        "Error validating access token: The session has been invalidated because the user "
        "changed their password or Facebook has changed the session for security reasons.",
    )
    assert isinstance(exc, LoginRequiredError)


# Рядовое протухание токена разлогином не считаем: действие оператора другое.
def test_classify_190_plain_token_expiry_is_not_login_required() -> None:
    exc = classify_graph_error(190, None, "Error validating access token")
    assert isinstance(exc, TokenInvalidError)
    assert not isinstance(exc, LoginRequiredError)


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


# MID X-16: login-subcode 463 (session expired) = разлогин → LoginRequiredError.
# Наследник TokenInvalidError (Permanent-класс), но с отдельным incident projection.
def test_subcode_463_login_required() -> None:
    exc = classify_graph_error(190, 463, "Session has expired")
    assert isinstance(exc, LoginRequiredError)
    assert isinstance(exc, TokenInvalidError)
    assert isinstance(exc, PermanentError)  # не ретраится бесконечно


# MID X-16: checkpoint-subcode 459 → LoginRequiredError (нужен ре-логин профиля).
def test_subcode_459_checkpoint_login_required() -> None:
    exc = classify_graph_error(190, 459, "Checkpoint required")
    assert isinstance(exc, LoginRequiredError)


# MID X-16: 190 БЕЗ login-subcode = рядовое протухание токена → TokenInvalidError,
# но НЕ LoginRequiredError (re-sniff чинит, ре-логин профиля не нужен).
def test_190_without_login_subcode_is_not_login_required() -> None:
    exc = classify_graph_error(190, None, "Error validating access token")
    assert isinstance(exc, TokenInvalidError)
    assert not isinstance(exc, LoginRequiredError)


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


# code -3 page-evaluate may happen after fetch/commit, so it is ambiguous rather
# than proven pre-send SessionUnavailable.  This distinction prevents blind
# retries of budget/create/duplicate operations.
def test_code_minus3_page_evaluate_is_ambiguous() -> None:
    exc = classify_graph_error(-3, None, "page.evaluate failed")
    assert isinstance(exc, AmbiguousResultError)
    assert not isinstance(exc, SessionUnavailableError)
    assert isinstance(exc, TemporaryError)
    assert exc.code == -3


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
