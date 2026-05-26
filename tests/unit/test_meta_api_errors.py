"""Unit-тесты для core/meta_api/errors.py — каталог ошибок Marketing API."""

from __future__ import annotations

import pytest

from core.meta_api.errors import (
    ActionBlockedError,
    DeprecatedApiError,
    InvalidRequestError,
    MetaApiError,
    PermissionDeniedError,
    RateLimitedError,
    SessionDeadError,
    TokenInvalidatedError,
    TransientError,
    classify_meta_api_error,
    is_retryable,
    is_token_problem,
    recommended_retry_delay,
)

# ─────────────────────────────────────────────────────────────────────────────
# classify_meta_api_error: известные коды
# ─────────────────────────────────────────────────────────────────────────────


# code=190 — инвалидация токена (пароль сменён / истёк TTL / и т.п.)
def test_classify_code_190_returns_token_invalidated():
    err = classify_meta_api_error(code=190, subcode=463, type_="OAuthException")
    assert isinstance(err, TokenInvalidatedError)


# code=190, subcode=460 — сменился пароль аккаунта
def test_classify_code_190_subcode_460():
    err = classify_meta_api_error(code=190, subcode=460)
    assert isinstance(err, TokenInvalidatedError)
    assert err.subcode == 460


# code=190, subcode=464 — пользователь не подтверждён
def test_classify_code_190_subcode_464():
    err = classify_meta_api_error(code=190, subcode=464)
    assert isinstance(err, TokenInvalidatedError)


# code=190, subcode=467 — невалидный токен
def test_classify_code_190_subcode_467():
    err = classify_meta_api_error(code=190, subcode=467)
    assert isinstance(err, TokenInvalidatedError)


# code=17 — User request limit reached
def test_classify_code_17_returns_rate_limited():
    err = classify_meta_api_error(code=17)
    assert isinstance(err, RateLimitedError)


# code=4 — Application request limit reached
def test_classify_code_4_returns_rate_limited():
    err = classify_meta_api_error(code=4)
    assert isinstance(err, RateLimitedError)


# code=32 — Page request limit reached
def test_classify_code_32_returns_rate_limited():
    err = classify_meta_api_error(code=32)
    assert isinstance(err, RateLimitedError)


# code=613 — Custom rate limit (subcode 1996, 1487742 и другие)
def test_classify_code_613_returns_rate_limited():
    err = classify_meta_api_error(code=613, subcode=1996)
    assert isinstance(err, RateLimitedError)
    assert err.subcode == 1996


# code=80004 — Too many calls to ad-account
def test_classify_code_80004_returns_rate_limited():
    err = classify_meta_api_error(code=80004)
    assert isinstance(err, RateLimitedError)


# code=10 — Permission denied (недостаточно scope)
def test_classify_code_10_returns_permission_denied():
    err = classify_meta_api_error(code=10)
    assert isinstance(err, PermissionDeniedError)


# code=200 — Permissions error (нет прав на кабинет/кампанию)
def test_classify_code_200_returns_permission_denied():
    err = classify_meta_api_error(code=200)
    assert isinstance(err, PermissionDeniedError)


# code=1 — Invalid request (generic)
def test_classify_code_1_returns_invalid_request():
    err = classify_meta_api_error(code=1)
    assert isinstance(err, InvalidRequestError)


# code=100 — Invalid parameter
def test_classify_code_100_returns_invalid_request():
    err = classify_meta_api_error(code=100)
    assert isinstance(err, InvalidRequestError)


# code=506 — Duplicate post, тоже относится к InvalidRequestError
def test_classify_code_506_returns_invalid_request():
    err = classify_meta_api_error(code=506)
    assert isinstance(err, InvalidRequestError)


# code=2 — Service temporary unavailable (retryable)
def test_classify_code_2_returns_transient():
    err = classify_meta_api_error(code=2)
    assert isinstance(err, TransientError)


# code=-1 — Токен не найден в page source (наш кастомный код browser-agent)
def test_classify_code_minus1_returns_session_dead():
    err = classify_meta_api_error(code=-1, type_="TokenNotFound")
    assert isinstance(err, SessionDeadError)


# code=-2 — Network error или timeout в page.evaluate(fetch(...))
def test_classify_code_minus2_returns_session_dead():
    err = classify_meta_api_error(code=-2, type_="NetworkError")
    assert isinstance(err, SessionDeadError)


# code=-3 — page.evaluate() выбросил исключение (браузер упал)
def test_classify_code_minus3_returns_session_dead():
    err = classify_meta_api_error(code=-3, type_="PageEvaluateError")
    assert isinstance(err, SessionDeadError)


# code=368 — Meta заблокировала действие (anti-spam)
def test_classify_code_368_returns_action_blocked():
    err = classify_meta_api_error(code=368)
    assert isinstance(err, ActionBlockedError)


# code=2635 — Используется устаревшая версия Ads API
def test_classify_code_2635_returns_deprecated_api():
    err = classify_meta_api_error(code=2635)
    assert isinstance(err, DeprecatedApiError)


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────


# Неизвестный code → базовый MetaApiError, не подкласс
def test_classify_unknown_code_returns_base_error():
    err = classify_meta_api_error(code=9999)
    assert type(err) is MetaApiError


# code=0 — нет ошибки (нулевой код) → базовый MetaApiError
def test_classify_code_zero_returns_base_error():
    err = classify_meta_api_error(code=0)
    assert type(err) is MetaApiError


# ─────────────────────────────────────────────────────────────────────────────
# Передача всех полей через classify
# ─────────────────────────────────────────────────────────────────────────────


# Все поля (code, subcode, type_, fbtrace_id, status_code, message) должны быть сохранены
def test_classify_preserves_all_fields():
    err = classify_meta_api_error(
        code=190,
        subcode=463,
        type_="OAuthException",
        message="Invalid OAuth access token signature.",
        fbtrace_id="AbcDef123",
        status_code=400,
    )
    assert err.code == 190
    assert err.subcode == 463
    assert err.type == "OAuthException"
    assert err.fbtrace_id == "AbcDef123"
    assert err.status_code == 400
    # Сообщение должно содержать код и оригинальный текст
    msg = str(err)
    assert "190" in msg
    assert "463" in msg


# fbtrace_id и status_code должны передаваться для rate limit тоже
def test_classify_rate_limit_preserves_fields():
    err = classify_meta_api_error(
        code=17,
        fbtrace_id="xyz789",
        status_code=429,
    )
    assert isinstance(err, RateLimitedError)
    assert err.fbtrace_id == "xyz789"
    assert err.status_code == 429


# ─────────────────────────────────────────────────────────────────────────────
# Прямое создание через конструктор
# ─────────────────────────────────────────────────────────────────────────────


# Создание базового MetaApiError напрямую без classify
def test_direct_construction_base_error():
    err = MetaApiError("тест", code=42, subcode=7, type_="TestType", fbtrace_id="abc")
    assert err.code == 42
    assert err.subcode == 7
    assert err.type == "TestType"
    assert err.fbtrace_id == "abc"
    assert str(err) == "тест"


# MetaApiError является подклассом Exception
def test_meta_api_error_is_exception():
    err = MetaApiError("тест")
    assert isinstance(err, Exception)


# Все специализированные классы наследуют MetaApiError
@pytest.mark.parametrize(
    "cls",
    [
        TokenInvalidatedError,
        RateLimitedError,
        PermissionDeniedError,
        InvalidRequestError,
        TransientError,
        SessionDeadError,
        ActionBlockedError,
        DeprecatedApiError,
    ],
)
def test_all_subclasses_inherit_meta_api_error(cls):
    # Любой специализированный класс должен наследовать MetaApiError
    err = cls("тест", code=1)
    assert isinstance(err, MetaApiError)


# ─────────────────────────────────────────────────────────────────────────────
# is_retryable
# ─────────────────────────────────────────────────────────────────────────────


# TransientError retryable
def test_is_retryable_transient():
    err = classify_meta_api_error(code=2)
    assert is_retryable(err) is True


# RateLimitedError retryable
def test_is_retryable_rate_limited():
    err = classify_meta_api_error(code=17)
    assert is_retryable(err) is True


# code=80004 (тоже RateLimitedError) — retryable
def test_is_retryable_rate_limited_80004():
    err = classify_meta_api_error(code=80004)
    assert is_retryable(err) is True


# TokenInvalidatedError не retryable
def test_is_not_retryable_token_invalidated():
    err = classify_meta_api_error(code=190)
    assert is_retryable(err) is False


# PermissionDeniedError не retryable
def test_is_not_retryable_permission_denied():
    err = classify_meta_api_error(code=200)
    assert is_retryable(err) is False


# InvalidRequestError не retryable
def test_is_not_retryable_invalid_request():
    err = classify_meta_api_error(code=100)
    assert is_retryable(err) is False


# SessionDeadError не retryable через is_retryable (требует bootstrap, не простой retry)
def test_is_not_retryable_session_dead():
    err = classify_meta_api_error(code=-1)
    assert is_retryable(err) is False


# ActionBlockedError не retryable
def test_is_not_retryable_action_blocked():
    err = classify_meta_api_error(code=368)
    assert is_retryable(err) is False


# DeprecatedApiError не retryable
def test_is_not_retryable_deprecated_api():
    err = classify_meta_api_error(code=2635)
    assert is_retryable(err) is False


# Базовый MetaApiError с неизвестным кодом не retryable
def test_is_not_retryable_base_error():
    err = classify_meta_api_error(code=9999)
    assert is_retryable(err) is False


# ─────────────────────────────────────────────────────────────────────────────
# is_token_problem
# ─────────────────────────────────────────────────────────────────────────────


# TokenInvalidatedError — проблема с токеном
def test_is_token_problem_token_invalidated():
    err = classify_meta_api_error(code=190)
    assert is_token_problem(err) is True


# SessionDeadError — тоже проблема с токеном (токен недоступен, сессия упала)
def test_is_token_problem_session_dead_minus1():
    err = classify_meta_api_error(code=-1)
    assert is_token_problem(err) is True


# SessionDeadError code=-2 — тоже проблема с токеном
def test_is_token_problem_session_dead_minus2():
    err = classify_meta_api_error(code=-2)
    assert is_token_problem(err) is True


# SessionDeadError code=-3
def test_is_token_problem_session_dead_minus3():
    err = classify_meta_api_error(code=-3)
    assert is_token_problem(err) is True


# RateLimitedError — не проблема с токеном
def test_is_not_token_problem_rate_limited():
    err = classify_meta_api_error(code=17)
    assert is_token_problem(err) is False


# TransientError — не проблема с токеном
def test_is_not_token_problem_transient():
    err = classify_meta_api_error(code=2)
    assert is_token_problem(err) is False


# PermissionDeniedError — не проблема с токеном
def test_is_not_token_problem_permission_denied():
    err = classify_meta_api_error(code=200)
    assert is_token_problem(err) is False


# ─────────────────────────────────────────────────────────────────────────────
# recommended_retry_delay
# ─────────────────────────────────────────────────────────────────────────────


# Для RateLimitedError задержка = 30 * attempt
def test_retry_delay_rate_limited_attempt1():
    err = classify_meta_api_error(code=17)
    assert recommended_retry_delay(err, attempt=1) == 30.0


# Для RateLimitedError attempt=3 → 90 секунд
def test_retry_delay_rate_limited_attempt3():
    err = classify_meta_api_error(code=4)
    assert recommended_retry_delay(err, attempt=3) == 90.0


# Для TransientError задержка = 5 * attempt
def test_retry_delay_transient_attempt1():
    err = classify_meta_api_error(code=2)
    assert recommended_retry_delay(err, attempt=1) == 5.0


# Для TransientError attempt=2 → 10 секунд
def test_retry_delay_transient_attempt2():
    err = classify_meta_api_error(code=2)
    assert recommended_retry_delay(err, attempt=2) == 10.0


# Для не-retryable ошибок задержка = 0.0
def test_retry_delay_non_retryable_token():
    err = classify_meta_api_error(code=190)
    assert recommended_retry_delay(err, attempt=1) == 0.0


# Для PermissionDeniedError задержка = 0.0
def test_retry_delay_permission_denied():
    err = classify_meta_api_error(code=200)
    assert recommended_retry_delay(err, attempt=5) == 0.0


# Для SessionDeadError задержка = 0.0 (нужен bootstrap, не retry)
def test_retry_delay_session_dead():
    err = classify_meta_api_error(code=-1)
    assert recommended_retry_delay(err, attempt=1) == 0.0


# Для базового MetaApiError (неизвестный код) задержка = 0.0
def test_retry_delay_base_error():
    err = classify_meta_api_error(code=9999)
    assert recommended_retry_delay(err, attempt=1) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# repr и строковое представление
# ─────────────────────────────────────────────────────────────────────────────


# repr должен содержать имя класса, code, subcode
def test_repr_contains_class_and_code():
    err = classify_meta_api_error(code=190, subcode=463)
    r = repr(err)
    assert "TokenInvalidatedError" in r
    assert "190" in r
    assert "463" in r


# Сообщение ошибки на русском должно содержать код
def test_message_contains_code():
    err = classify_meta_api_error(code=17)
    assert "17" in str(err)
