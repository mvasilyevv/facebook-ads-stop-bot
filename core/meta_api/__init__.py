# -*- coding: utf-8 -*-
"""Marketing API channel: тонкий клиент над gRPC к browser-agent.

Архитектурно: вызовы Graph API исполняются изнутри активной Vision-сессии
через page.evaluate(fetch(...)). Здесь — только Python-обвязка:
- MetaApiClient (gRPC wrapper)
- frozen schemas (MetaApiAdRow, MetaInsightsRow, MetaMutationPayload)
- классификация ошибок Graph → доменные exceptions
- outbox (task_queue.task_type='meta_api_mutation')
- audit log в meta_api_audit_log (partitioned by month)

См. META_INTEGRATION_PLAN.md § 3-4.
"""

from core.meta_api.errors import (
    MetaApiError,
    NotFoundError,
    PermanentError,
    PermissionError,
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
    TokenInvalidError,
    classify_graph_error,
)
from core.meta_api.schemas import (
    MUTATION_KINDS,
    MetaApiAdRow,
    MetaInsightsRequest,
    MetaInsightsRow,
    MetaMutationPayload,
)

__all__ = [
    "MUTATION_KINDS",
    "MetaApiAdRow",
    "MetaApiError",
    "MetaInsightsRequest",
    "MetaInsightsRow",
    "MetaMutationPayload",
    "NotFoundError",
    "PermanentError",
    "PermissionError",
    "RateLimitedError",
    "SessionUnavailableError",
    "TemporaryError",
    "TokenInvalidError",
    "classify_graph_error",
]
