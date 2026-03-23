from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.notifier.events import TelegramEvent as TelegramEventDTO
from apps.notifier.events import TelegramEventPayload, TelegramEventType
from apps.notifier.telegram import TelegramNotifier
from core.repositories.notification_outbox import NotificationOutboxRepository


class OutboxProcessor:
    """Обрабатывает outbox-записи и отправляет Telegram-уведомления."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._notifier = notifier

    async def process_pending(self, batch_size: int = 50) -> int:
        """Обрабатывает пакет ожидающих событий. Возвращает количество обработанных."""

        logger = logging.getLogger(__name__)

        if self._notifier is None:
            return 0

        async with self._session_factory() as session:
            repo = NotificationOutboxRepository(session)
            pending = await repo.list_pending(limit=batch_size)

            if not pending:
                return 0

            processed = 0
            for db_event in pending:
                try:
                    dto_event = self._to_dto(db_event)
                    self._notifier.notify(dto_event)
                    await repo.mark_sent(str(db_event.id))
                    processed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Не удалось отправить событие %s: %s",
                        db_event.id,
                        exc,
                    )
                    await repo.mark_failed(str(db_event.id), str(exc))

            await session.commit()
            logger.info("Обработано %s из %s outbox-событий", processed, len(pending))
            return processed

    @staticmethod
    def _to_dto(db_event) -> TelegramEventDTO:
        """Конвертирует DB-модель в DTO для notifier."""

        from datetime import UTC, datetime

        payload_data = db_event.payload_json or {}
        return TelegramEventDTO(
            event_type=TelegramEventType(db_event.event_type),
            dedupe_key=f"{db_event.event_type}:{db_event.id}",
            created_at=datetime.now(tz=UTC),
            payload=TelegramEventPayload(
                host=payload_data.get("host", "unknown"),
                account_name=payload_data.get("account_name", "unknown"),
                campaign_name=payload_data.get("campaign_name", "unknown"),
                adset_name=payload_data.get("adset_name", "unknown"),
                ad_name=payload_data.get("ad_name", "unknown"),
                fb_ad_id=payload_data.get("fb_ad_id", "unknown"),
                reason=payload_data.get("reason", ""),
                metrics=payload_data.get("metrics", {}),
                delivery_before=payload_data.get("delivery_before"),
                delivery_after=payload_data.get("delivery_after"),
                rule_id=payload_data.get("rule_id"),
                extra=payload_data.get("extra", {}),
            ),
        )
