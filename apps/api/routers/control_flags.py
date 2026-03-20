from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.control_flags import ControlFlagCreateRequest, ControlFlagItem
from core.domain import EntityType, TrackingMode
from core.repositories import ControlFlagsRepository

router = APIRouter(prefix="/control-flags", tags=["control-flags"])


def _map_control_flag_item(flag) -> ControlFlagItem:
    return ControlFlagItem(
        id=str(flag.id),
        entity_type=flag.entity_type.value,
        entity_external_id=flag.entity_id,
        tracking_mode=flag.tracking_mode.value,
        reason=flag.reason,
        created_by=flag.created_by,
        created_at=flag.created_at,
        expires_at=flag.expires_at,
    )


@router.get("", response_model=list[ControlFlagItem])
async def list_control_flags(session: DbSessionDep) -> list[ControlFlagItem]:
    repo = ControlFlagsRepository(session)
    flags = await repo.list_control_flags()
    return [_map_control_flag_item(flag) for flag in flags]


@router.post("", response_model=ControlFlagItem, status_code=status.HTTP_201_CREATED)
async def create_control_flag(
    payload: ControlFlagCreateRequest,
    session: DbSessionDep,
) -> ControlFlagItem:
    repo = ControlFlagsRepository(session)
    flag = await repo.upsert_control_flag(
        entity_type=EntityType(payload.entity_type.value),
        entity_id=payload.entity_external_id,
        reason=payload.reason,
        created_by=payload.created_by,
        tracking_mode=TrackingMode(payload.tracking_mode.value),
        expires_at=payload.expires_at,
    )
    await session.commit()
    return _map_control_flag_item(flag)


@router.delete("/{flag_id}", response_model=ControlFlagItem)
async def delete_control_flag(flag_id: str, session: DbSessionDep) -> ControlFlagItem:
    repo = ControlFlagsRepository(session)
    flags = await repo.list_control_flags()
    target_flag = next((flag for flag in flags if str(flag.id) == flag_id), None)
    if target_flag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Флаг управления не найден"
        )
    await repo.delete_control_flag(target_flag.entity_type, target_flag.entity_id)
    await session.commit()
    return _map_control_flag_item(target_flag)
