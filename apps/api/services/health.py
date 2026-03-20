from __future__ import annotations

from datetime import UTC, datetime

from apps.api.config import ApiSettings
from apps.api.schemas.common import HealthResponse
from core.db import check_database_connection


class HealthService:
    def __init__(self, settings: ApiSettings) -> None:
        self._settings = settings

    async def get_health(self) -> HealthResponse:
        database_is_healthy = await check_database_connection()
        return HealthResponse(
            status="ok" if database_is_healthy else "degraded",
            service="api",
            environment=self._settings.environment,
            database_status="ok" if database_is_healthy else "unavailable",
            timestamp=datetime.now(tz=UTC),
        )
