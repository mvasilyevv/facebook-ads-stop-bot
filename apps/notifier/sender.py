from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from apps.notifier.errors import TelegramDeliveryError
from apps.notifier.events import TelegramEvent


class TelegramTransport(Protocol):
    def send(self, text: str) -> None: ...


@dataclass(slots=True)
class InMemoryDedupStore:
    ttl_seconds: int = 300
    _items: dict[str, float] = field(default_factory=dict)

    def is_duplicate(self, key: str) -> bool:
        now = time.time()
        self._cleanup(now)
        expires_at = self._items.get(key)
        return expires_at is not None and expires_at > now

    def remember(self, key: str) -> None:
        now = time.time()
        self._cleanup(now)
        self._items[key] = now + self.ttl_seconds

    def _cleanup(self, now: float) -> None:
        expired_keys = [key for key, expires_at in self._items.items() if expires_at <= now]
        for key in expired_keys:
            self._items.pop(key, None)


class TelegramSender:
    """Отправляет сообщения в Telegram с дедупликацией и повторными попытками."""

    def __init__(self, transport: TelegramTransport, dedup_store: InMemoryDedupStore) -> None:
        self._transport = transport
        self._dedup_store = dedup_store

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(TelegramDeliveryError),
        reraise=True,
    )
    def send(self, event: TelegramEvent, text: str) -> bool:
        if self._dedup_store.is_duplicate(event.dedupe_key):
            return False

        try:
            self._transport.send(text)
        except Exception as exc:  # noqa: BLE001
            raise TelegramDeliveryError("Не удалось отправить сообщение в Telegram") from exc

        self._dedup_store.remember(event.dedupe_key)
        return True
