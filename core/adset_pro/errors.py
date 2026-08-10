# -*- coding: utf-8 -*-
"""Доменные исключения AdSet.pro клиента.

Иерархия параллельна core/meta_api/errors.py:
- AdsetProError — базовое.
- AuthError — 401/403 либо невалидный API key (permanent, не retry).
- RateLimitedError — 429 либо message содержит throttle/rate limit (temporary).
- NotFoundError — 404 (permanent для конкретного объекта).
- TemporaryError — 5xx и сетевые сбои (retry).
"""

from __future__ import annotations


class AdsetProError(RuntimeError):
    """Базовое исключение для всех ошибок AdSet.pro REST API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        endpoint: str | None = None,
        response_body: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.endpoint = endpoint
        self.response_body = response_body
        super().__init__(message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status_code={self.status_code}, "
            f"endpoint={self.endpoint!r}, message={super().__str__()!r})"
        )


class TemporaryError(AdsetProError):
    """Временная ошибка — стоит retry (5xx, сеть)."""


class PermanentError(AdsetProError):
    """Постоянная ошибка — retry не поможет."""


class AuthError(PermanentError):
    """401/403 — невалидный API key либо нет прав."""


class NotFoundError(PermanentError):
    """404 — объект не найден."""


class RateLimitedError(TemporaryError):
    """429 либо throttle/rate-limit от AdSet.pro."""


def classify_http_error(
    status_code: int,
    message: str,
    *,
    endpoint: str | None = None,
    response_body: str | None = None,
) -> AdsetProError:
    """Подобрать класс исключения по HTTP-статусу ответа AdSet.pro.

    Используется в client.py при не-2xx ответе.
    """
    cls: type[AdsetProError]
    if status_code in (401, 403):
        cls = AuthError
    elif status_code == 404:
        cls = NotFoundError
    elif status_code == 429:
        cls = RateLimitedError
    elif 500 <= status_code < 600:
        cls = TemporaryError
    else:
        cls = PermanentError
    return cls(
        message or f"AdSet.pro HTTP {status_code}",
        status_code=status_code,
        endpoint=endpoint,
        response_body=response_body,
    )
