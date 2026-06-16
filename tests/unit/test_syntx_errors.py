# -*- coding: utf-8 -*-
"""Unit: классификация HTTP-ошибок syntx + детектор модерации."""

from __future__ import annotations

import pytest

from core.syntx.errors import (
    PermanentError,
    SyntxAuthError,
    SyntxModerationError,
    SyntxNotFoundError,
    SyntxRateLimitedError,
    TemporaryError,
    classify_http_error,
    looks_like_moderation,
)


# 401/403 → SyntxAuthError (постоянная — токен протух/невалиден, не ретраим).
@pytest.mark.parametrize("status", [401, 403])
def test_classify_auth(status: int) -> None:
    exc = classify_http_error(status, "Unauthorized", endpoint="/user/balance")
    assert isinstance(exc, SyntxAuthError)
    assert isinstance(exc, PermanentError)
    assert exc.status_code == status


# 404 → NotFound.
def test_classify_not_found() -> None:
    assert isinstance(classify_http_error(404, "no chat"), SyntxNotFoundError)


# 429 → RateLimited (временная).
def test_classify_rate_limited() -> None:
    exc = classify_http_error(429, "slow down")
    assert isinstance(exc, SyntxRateLimitedError)
    assert isinstance(exc, TemporaryError)


# 5xx → TemporaryError (ретраим).
@pytest.mark.parametrize("status", [500, 502, 503])
def test_classify_5xx_temporary(status: int) -> None:
    assert isinstance(classify_http_error(status, "boom"), TemporaryError)


# Прочая 4xx (418) → PermanentError, не ретраим.
def test_classify_unknown_4xx_permanent() -> None:
    exc = classify_http_error(418, "teapot")
    assert isinstance(exc, PermanentError)
    assert not isinstance(exc, TemporaryError)


# Маркер модерации в теле перебивает статус → SyntxModerationError (гемблинг-кейс).
def test_classify_moderation_overrides_status() -> None:
    exc = classify_http_error(400, "bad", response_body='{"detail":"image_violation"}')
    assert isinstance(exc, SyntxModerationError)
    assert isinstance(exc, PermanentError)


# looks_like_moderation ловит известные маркеры и игнорирует обычный текст.
@pytest.mark.parametrize(
    "text,expected",
    [
        ("face_detected in upload", True),
        ("VIDEO_REVIEW_FAILED", True),
        ("text_violation", True),
        ("просто ошибка сети", False),
        (None, False),
        ("", False),
    ],
)
def test_looks_like_moderation(text: str | None, expected: bool) -> None:
    assert looks_like_moderation(text) is expected
