from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.services.state import ApiState
from core.db import get_session_factory


def get_api_state(request: Request) -> ApiState:
    state = getattr(request.app.state, "api_state", None)
    if state is None:
        raise RuntimeError("Состояние API не инициализировано")
    return state


async def get_db_session() -> AsyncSession:
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


ApiStateDep = Annotated[ApiState, Depends(get_api_state)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
