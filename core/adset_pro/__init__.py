# -*- coding: utf-8 -*-
"""Типизированный MCP-клиент и durable postback-контур AdSet.pro.

Публичный API:
- AdsetProClient — async REST-клиент.
- StatsQueryRequest / StatsQueryResponse / ConversionRow / PostbackEvent — DTO.
- AdsetProError и потомки — иерархия исключений.
"""

from core.adset_pro.client import AdsetProClient
from core.adset_pro.credentials import (
    AdsetProCredentials,
    bootstrap_adsetpro_credentials_from_env,
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
from core.adset_pro.schemas import (
    ConversionRow,
    PostbackEvent,
    StatsQueryRequest,
    StatsQueryResponse,
)

__all__ = [
    "AdsetProClient",
    "AdsetProCredentials",
    "bootstrap_adsetpro_credentials_from_env",
    "AdsetProError",
    "AuthError",
    "ConversionRow",
    "IngestResult",
    "NotFoundError",
    "PermanentError",
    "PostbackEvent",
    "RateLimitedError",
    "StatsQueryRequest",
    "StatsQueryResponse",
    "TemporaryError",
    "create_adsetpro_client",
    "ingest_postback",
    "load_adsetpro_credentials",
    "resolve_adsetpro_api_key",
    "resolve_adsetpro_postback_secret",
    "upsert_adsetpro_credentials",
]
