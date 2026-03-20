from __future__ import annotations

from fastapi import APIRouter

from apps.api.deps import ApiStateDep
from apps.api.schemas.decisions import DecisionItem

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.get("", response_model=list[DecisionItem])
async def list_decisions(api_state: ApiStateDep) -> list[DecisionItem]:
    return api_state.decisions_service.list_decisions()
