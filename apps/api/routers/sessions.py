from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from apps.api.deps import ApiStateDep
from apps.api.schemas.sessions import (
    BrowserSessionItem,
    SessionActionResponse,
    SessionControlRequest,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[BrowserSessionItem])
async def list_sessions(api_state: ApiStateDep) -> list[BrowserSessionItem]:
    return api_state.sessions_service.list_sessions()


@router.get("/{profile_id}", response_model=BrowserSessionItem)
async def get_session(profile_id: str, api_state: ApiStateDep) -> BrowserSessionItem:
    session = api_state.sessions_service.get_session(profile_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сессия не найдена")
    return session


@router.post("/{profile_id}/start", response_model=SessionActionResponse)
async def start_session(
    profile_id: str, payload: SessionControlRequest, api_state: ApiStateDep
) -> SessionActionResponse:
    result = api_state.sessions_service.start_session(profile_id, payload)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сессия не найдена")
    return result


@router.post("/{profile_id}/stop", response_model=SessionActionResponse)
async def stop_session(
    profile_id: str, payload: SessionControlRequest, api_state: ApiStateDep
) -> SessionActionResponse:
    result = api_state.sessions_service.stop_session(profile_id, payload)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сессия не найдена")
    return result
