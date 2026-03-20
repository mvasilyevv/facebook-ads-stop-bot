from __future__ import annotations

from apps.notifier.events import TelegramEvent
from apps.notifier.formatter import TelegramMessageFormatter
from apps.notifier.sender import TelegramSender


class TelegramNotifier:
    """Фасад уведомлений Telegram."""

    def __init__(self, formatter: TelegramMessageFormatter, sender: TelegramSender) -> None:
        self._formatter = formatter
        self._sender = sender

    def notify(self, event: TelegramEvent) -> bool:
        text = self._formatter.format(event)
        return self._sender.send(event, text)
