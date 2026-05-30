# -*- coding: utf-8 -*-
"""Минимальный REST-клиент AdSet.pro (Этап 6 META_INTEGRATION_PLAN).

Публичный API:
- AdsetProClient — async REST-клиент.
- StatsQueryRequest / StatsQueryResponse / ConversionRow / PostbackEvent — DTO.
- AdsetProError и потомки — иерархия исключений.

См. META_INTEGRATION_PLAN.md §4.4 (структура канала) и §5 Волна 3 (БД на Этапе 6).
"""

from core.adset_pro.aggregator import AggregationResult, aggregate_postback_events
from core.adset_pro.client import AdsetProClient
from core.adset_pro.credentials import (
    AdsetProCredentials,
    create_adsetpro_client,
    load_adsetpro_credentials,
    resolve_adsetpro_api_key,
    resolve_adsetpro_postback_secret,
    upsert_adsetpro_credentials,
)
from core.adset_pro.errors import (
    AdsetProError,
    AuthError,
    NotFoundError,
    PermanentError,
    RateLimitedError,
    TemporaryError,
)
from core.adset_pro.ingest import IngestResult, ingest_postback
from core.adset_pro.outgoing import (
    OutgoingPostback,
    OutgoingPostbackSender,
    OutgoingResult,
    build_postback_url,
)
from core.adset_pro.schemas import (
    ConversionRow,
    PostbackEvent,
    StatsQueryRequest,
    StatsQueryResponse,
)

__all__ = [
    "AdsetProClient",
    "AdsetProCredentials",
    "AdsetProError",
    "AggregationResult",
    "AuthError",
    "ConversionRow",
    "IngestResult",
    "NotFoundError",
    "OutgoingPostback",
    "OutgoingPostbackSender",
    "OutgoingResult",
    "PermanentError",
    "PostbackEvent",
    "RateLimitedError",
    "StatsQueryRequest",
    "StatsQueryResponse",
    "TemporaryError",
    "aggregate_postback_events",
    "build_postback_url",
    "create_adsetpro_client",
    "ingest_postback",
    "load_adsetpro_credentials",
    "resolve_adsetpro_api_key",
    "resolve_adsetpro_postback_secret",
    "upsert_adsetpro_credentials",
]
