from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import TelegramEventType
from core.models.operations import TelegramEvent
from core.repositories.base import AsyncRepository


class NotificationOutboxRepository(AsyncRepository):
    """Репозиторий для outbox-записей Telegram-уведомлений."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def enqueue(
        self,
        *,
        decision_id: Any | None,
        event_type: TelegramEventType,
        payload_json: dict,
    ) -> TelegramEvent:
        """Добавляет событие в outbox для последующей отправки."""

        event = TelegramEvent(
            decision_id=decision_id,
            event_type=event_type,
            payload_json=payload_json,
            status="pending",
            sent_at=None,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_pending(self, limit: int = 50) -> list[TelegramEvent]:
        """Возвращает неотправленные события для обработки."""

        stmt = (
            select(TelegramEvent)
            .where(TelegramEvent.status == "pending")
            .order_by(TelegramEvent.id)
            .limit(limit)
        )
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def mark_sent(self, event_id: str) -> None:
        """Отмечает событие как успешно отправленное."""

        await self.session.execute(
            update(TelegramEvent)
            .where(TelegramEvent.id == event_id)
            .values(status="sent", sent_at=datetime.now(tz=UTC))
        )

    async def mark_failed(self, event_id: str, error: str) -> None:
        """Отмечает событие как неуспешное."""

        await self.session.execute(
            update(TelegramEvent)
            .where(TelegramEvent.id == event_id)
            .values(status=f"failed:{error[:200]}")
        )
