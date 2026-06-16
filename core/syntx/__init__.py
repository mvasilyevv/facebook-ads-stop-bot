# -*- coding: utf-8 -*-
"""core.syntx — прямой API-клиент syntx.ai для генерации креативов (без UI).

Image-генерация рабочая; video — заложена (SyntxClient.generate_video) под
будущую интеграцию. Публичный контракт + контракт API см. client.py / память
reference-syntx-api-direct.
"""

from __future__ import annotations

from core.syntx.auth import decode_token_exp, resolve_syntx_token, token_days_left
from core.syntx.catalog import ModelCatalog
from core.syntx.client import SyntxClient
from core.syntx.errors import (
    PermanentError,
    SyntxAuthError,
    SyntxError,
    SyntxGenerationError,
    SyntxGenerationTimeout,
    SyntxModerationError,
    SyntxNotFoundError,
    SyntxRateLimitedError,
    TemporaryError,
    classify_http_error,
)
from core.syntx.models import (
    SCOPE_AUDIO,
    SCOPE_IMAGE,
    SCOPE_TEXT,
    SCOPE_VIDEO,
    Balance,
    GenRequest,
    GenResult,
    ModelInfo,
    UploadedRef,
)

__all__ = [
    "SyntxClient",
    "ModelCatalog",
    "GenRequest",
    "GenResult",
    "ModelInfo",
    "UploadedRef",
    "Balance",
    "SCOPE_IMAGE",
    "SCOPE_VIDEO",
    "SCOPE_AUDIO",
    "SCOPE_TEXT",
    "resolve_syntx_token",
    "decode_token_exp",
    "token_days_left",
    "SyntxError",
    "TemporaryError",
    "PermanentError",
    "SyntxAuthError",
    "SyntxNotFoundError",
    "SyntxRateLimitedError",
    "SyntxModerationError",
    "SyntxGenerationError",
    "SyntxGenerationTimeout",
    "classify_http_error",
]
