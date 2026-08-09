"""Owner-only, server-authoritative operator presentation preferences."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.operator_preferences import (
    OperatorDisplayPreferencePutRequest,
    OperatorDisplayPreferenceResponse,
)
from apps.api.schemas.problem import ApiProblem
from core.operator.display_preferences import (
    ActiveOwnerRequiredError,
    OperatorDisplayPreferenceSnapshot,
    get_operator_display_preference,
    put_operator_display_preference,
)

router = APIRouter(prefix="/operator/preferences", tags=["operator"])

_PROBLEM_RESPONSES = {
    401: {"model": ApiProblem, "description": "Authentication failed"},
    403: {"model": ApiProblem, "description": "Active owner required"},
    422: {"model": ApiProblem, "description": "Request validation failed"},
    503: {"model": ApiProblem, "description": "Preference store unavailable"},
}


def _owner_telegram_user_id(request: Request) -> int:
    value = getattr(request.state, "operator_owner_telegram_user_id", None)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise HTTPException(
            status_code=403,
            detail="Не удалось подтвердить профиль владельца",
        )
    return value


def _response(
    snapshot: OperatorDisplayPreferenceSnapshot,
) -> OperatorDisplayPreferenceResponse:
    return OperatorDisplayPreferenceResponse(
        timezone_name=snapshot.timezone_name,
        updated_at=snapshot.updated_at,
    )


@router.get(
    "/display",
    response_model=OperatorDisplayPreferenceResponse,
    responses=_PROBLEM_RESPONSES,
)
async def get_display_preference(
    request: Request,
    response: Response,
    engine: DepEngine,
) -> OperatorDisplayPreferenceResponse:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        snapshot = await get_operator_display_preference(
            engine,
            telegram_user_id=_owner_telegram_user_id(request),
        )
    except ActiveOwnerRequiredError as exc:
        raise HTTPException(
            status_code=403,
            detail="Настройка отображения доступна только активному владельцу",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Хранилище настройки отображения временно недоступно",
        ) from exc
    return _response(snapshot)


@router.put(
    "/display",
    response_model=OperatorDisplayPreferenceResponse,
    responses=_PROBLEM_RESPONSES,
)
async def put_display_preference(
    body: OperatorDisplayPreferencePutRequest,
    request: Request,
    response: Response,
    engine: DepEngine,
) -> OperatorDisplayPreferenceResponse:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        snapshot = await put_operator_display_preference(
            engine,
            telegram_user_id=_owner_telegram_user_id(request),
            timezone_name=body.timezone_name,
        )
    except ActiveOwnerRequiredError as exc:
        raise HTTPException(
            status_code=403,
            detail="Настройка отображения доступна только активному владельцу",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Хранилище настройки отображения временно недоступно",
        ) from exc
    return _response(snapshot)


__all__ = ["router"]
