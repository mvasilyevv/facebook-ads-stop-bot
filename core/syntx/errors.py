# -*- coding: utf-8 -*-
"""Доменные исключения syntx.ai клиента.

Иерархия параллельна core/adset_pro/errors.py:
- SyntxError       — базовое.
- TemporaryError   — 5xx и сетевые сбои (retry).
- PermanentError   — постоянные (retry не поможет).
- SyntxAuthError   — 401/403 либо протухший/невалидный JWT (permanent).
- SyntxNotFoundError — 404.
- SyntxRateLimitedError — 429 (temporary).
- SyntxModerationError — контент зарезан модерацией (гемблинг: face_detected /
  image_violation / video_review_failed / text_violation). Permanent — тот же
  промпт/реф пройдёт только после правки материала.
- SyntxGenerationError — генерация завершилась без результата (без явной модерации).
- SyntxGenerationTimeout — поллинг не дождался завершения.

См. reference-syntx-api-direct (память) — контракт снят 16.06.
"""

from __future__ import annotations

# Маркеры модерации из i18n errors-namespace syntx (api_errors.ai.generations.*).
# Для гемблинга критично: seedance/veo режут лица и «violation» — ловим заранее.
_MODERATION_MARKERS: tuple[str, ...] = (
    "face_detected",
    "image_violation",
    "video_review_failed",
    "text_violation",
    "moderation",
    "violation",
    "nsfw",
    "content_policy",
)


class SyntxError(RuntimeError):
    """Базовое исключение для всех ошибок syntx.ai API."""

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


class TemporaryError(SyntxError):
    """Временная ошибка — стоит retry (5xx, сеть)."""


class PermanentError(SyntxError):
    """Постоянная ошибка — retry не поможет."""


class SyntxAuthError(PermanentError):
    """401/403 — JWT протух (30 дней) либо невалиден. Обновить auth_token."""


class SyntxNotFoundError(PermanentError):
    """404 — объект (чат/модель) не найден."""


class SyntxRateLimitedError(TemporaryError):
    """429 либо throttle от syntx."""


class SyntxModerationError(PermanentError):
    """Материал зарезан модерацией нейросети (см. _MODERATION_MARKERS)."""


class SyntxGenerationError(PermanentError):
    """Генерация завершилась, но результата нет (и это не явная модерация)."""


class SyntxGenerationTimeout(TemporaryError):
    """Поллинг /inprogress не дождался завершения за poll_timeout."""


def looks_like_moderation(text: str | None) -> bool:
    """True, если в тексте ошибки есть маркер модерации (case-insensitive)."""
    if not text:
        return False
    low = text.lower()
    return any(marker in low for marker in _MODERATION_MARKERS)


def classify_http_error(
    status_code: int,
    message: str,
    *,
    endpoint: str | None = None,
    response_body: str | None = None,
) -> SyntxError:
    """Подобрать класс исключения по HTTP-статусу ответа syntx.

    Модерация может приехать и под 4xx с маркером в теле — ловим её раньше
    общей 4xx-ветки, иначе попадёт в PermanentError без понятной причины.
    """
    if looks_like_moderation(response_body) or looks_like_moderation(message):
        cls: type[SyntxError] = SyntxModerationError
    elif status_code in (401, 403):
        cls = SyntxAuthError
    elif status_code == 404:
        cls = SyntxNotFoundError
    elif status_code == 429:
        cls = SyntxRateLimitedError
    elif 500 <= status_code < 600:
        cls = TemporaryError
    else:
        cls = PermanentError
    return cls(
        message or f"syntx HTTP {status_code}",
        status_code=status_code,
        endpoint=endpoint,
        response_body=response_body,
    )
