from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from apps.api.schemas.common import ControlFlagTarget, TrackingMode
from apps.api.schemas.control_flags import ControlFlagCreateRequest, ControlFlagItem
from apps.api.services.state import ApiStore


class ControlFlagsService:
    def __init__(self, store: ApiStore) -> None:
        self._store = store

    def list_control_flags(self) -> list[ControlFlagItem]:
        return list(self._store.control_flags.values())

    def create_control_flag(self, payload: ControlFlagCreateRequest) -> ControlFlagItem:
        flag = ControlFlagItem(
            id=str(uuid4()),
            entity_type=payload.entity_type,
            entity_external_id=payload.entity_external_id,
            tracking_mode=payload.tracking_mode,
            reason=payload.reason,
            created_by=payload.created_by,
            created_at=datetime.now(tz=UTC),
            expires_at=payload.expires_at,
        )
        self._store.control_flags[flag.id] = flag
        return flag

    def delete_control_flag(self, flag_id: str) -> ControlFlagItem | None:
        return self._store.control_flags.pop(flag_id, None)

    def create_or_replace_entity_flag(
        self,
        entity_type: ControlFlagTarget,
        entity_external_id: str,
        tracking_mode: TrackingMode,
        reason: str,
        created_by: str,
    ) -> ControlFlagItem:
        existing_flag_id = next(
            (
                flag_id
                for flag_id, flag in self._store.control_flags.items()
                if flag.entity_type == entity_type and flag.entity_external_id == entity_external_id
            ),
            None,
        )
        if existing_flag_id is not None:
            self._store.control_flags.pop(existing_flag_id)
        payload = ControlFlagCreateRequest(
            entity_type=entity_type,
            entity_external_id=entity_external_id,
            tracking_mode=tracking_mode,
            reason=reason,
            created_by=created_by,
        )
        return self.create_control_flag(payload)

    def remove_entity_flag(self, entity_type: ControlFlagTarget, entity_external_id: str) -> None:
        flag_id = next(
            (
                flag_id
                for flag_id, flag in self._store.control_flags.items()
                if flag.entity_type == entity_type and flag.entity_external_id == entity_external_id
            ),
            None,
        )
        if flag_id is not None:
            self._store.control_flags.pop(flag_id, None)
