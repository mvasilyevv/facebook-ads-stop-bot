from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.notifier.errors import TelegramDeliveryError
from apps.notifier.events import TelegramEvent, TelegramEventPayload, TelegramEventType
from apps.notifier.sender import InMemoryDedupStore, TelegramSender


class DummyTransport:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("Временная ошибка транспорта")
        self.messages.append(text)


def _build_event() -> TelegramEvent:
    return TelegramEvent(
        event_type=TelegramEventType.AD_PAUSED_BY_BOT,
        dedupe_key="dedupe-key",
        created_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        payload=TelegramEventPayload(
            host="browser-host-01",
            account_name="acc-1",
            campaign_name="Кампания",
            adset_name="Адсет",
            ad_name="Объявление",
            fb_ad_id="123",
            reason="Причина теста",
            metrics={},
        ),
    )


# Проверяет, что дедупликация не позволяет отправить одно и то же событие повторно в пределах TTL.
def test_sender_skips_duplicate_event() -> None:
    transport = DummyTransport()
    sender = TelegramSender(transport=transport, dedup_store=InMemoryDedupStore(ttl_seconds=60))
    event = _build_event()

    first_result = sender.send(event, "Первое сообщение")
    second_result = sender.send(event, "Повторное сообщение")

    assert first_result is True
    assert second_result is False
    assert transport.messages == ["Первое сообщение"]


# Проверяет, что отправщик пробрасывает русскую ошибку после исчерпания повторных попыток.
def test_sender_raises_russian_error_after_retries() -> None:
    transport = DummyTransport(fail_times=3)
    sender = TelegramSender(transport=transport, dedup_store=InMemoryDedupStore(ttl_seconds=60))

    with pytest.raises(TelegramDeliveryError, match="Не удалось отправить сообщение в Telegram"):
        sender.send(_build_event(), "Сообщение")
