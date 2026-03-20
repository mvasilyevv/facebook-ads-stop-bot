from __future__ import annotations

from fastapi import APIRouter

from apps.api.deps import ApiStateDep
from apps.api.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def get_health(api_state: ApiStateDep) -> HealthResponse:
    return await api_state.health_service.get_health()
