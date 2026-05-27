# -*- coding: utf-8 -*-
"""Минимальный REST-клиент AdSet.pro (Этап 6 META_INTEGRATION_PLAN).

Публичный API:
- AdsetProClient — async REST-клиент.
- StatsQueryRequest / StatsQueryResponse / ConversionRow / PostbackEvent — DTO.
- AdsetProError и потомки — иерархия исключений.

См. META_INTEGRATION_PLAN.md §4.4 (структура канала) и §5 Волна 3 (БД на Этапе 6).
"""

from core.adset_pro.client import AdsetProClient
from core.adset_pro.errors import (
    AdsetProError,
    AuthError,
    NotFoundError,
    PermanentError,
    RateLimitedError,
    TemporaryError,
)
from core.adset_pro.schemas import (
    ConversionRow,
    PostbackEvent,
    StatsQueryRequest,
    StatsQueryResponse,
)

__all__ = [
    "AdsetProClient",
    "AdsetProError",
    "AuthError",
    "ConversionRow",
    "NotFoundError",
    "PermanentError",
    "PostbackEvent",
    "RateLimitedError",
    "StatsQueryRequest",
    "StatsQueryResponse",
    "TemporaryError",
]
