from __future__ import annotations

from datetime import UTC, datetime

from apps.api.schemas.common import SessionStatus
from apps.api.schemas.sessions import (
    BrowserSessionItem,
    SessionActionResponse,
    SessionControlRequest,
)
from apps.api.services.state import ApiStore


class SessionsService:
    def __init__(self, store: ApiStore) -> None:
        self._store = store

    def list_sessions(self) -> list[BrowserSessionItem]:
        return list(self._store.sessions.values())

    def get_session(self, profile_id: str) -> BrowserSessionItem | None:
        return self._store.sessions.get(profile_id)

    def start_session(
        self, profile_id: str, payload: SessionControlRequest
    ) -> SessionActionResponse | None:
        session = self._store.sessions.get(profile_id)
        if session is None:
            return None
        now = datetime.now(tz=UTC)
        updated_session = session.model_copy(
            update={
                "browser_host_id": payload.browser_host_id,
                "status": SessionStatus.ACTIVE,
                "last_started_at": now,
                "last_message": f"Сессия запущена. Причина: {payload.reason}",
            }
        )
        self._store.sessions[profile_id] = updated_session
        return SessionActionResponse(
            message="Сессия успешно запущена",
            session=updated_session,
        )

    def stop_session(
        self, profile_id: str, payload: SessionControlRequest
    ) -> SessionActionResponse | None:
        session = self._store.sessions.get(profile_id)
        if session is None:
            return None
        now = datetime.now(tz=UTC)
        updated_session = session.model_copy(
            update={
                "browser_host_id": payload.browser_host_id,
                "status": SessionStatus.STOPPED,
                "last_stopped_at": now,
                "last_message": f"Сессия остановлена. Причина: {payload.reason}",
            }
        )
        self._store.sessions[profile_id] = updated_session
        return SessionActionResponse(
            message="Сессия успешно остановлена",
            session=updated_session,
        )
