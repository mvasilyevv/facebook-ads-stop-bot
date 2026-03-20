from __future__ import annotations

from fastapi import APIRouter

from apps.api.config import load_settings
from apps.api.schemas.common import HealthResponse
from apps.api.services.health import HealthService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    return await HealthService(load_settings()).get_health()
