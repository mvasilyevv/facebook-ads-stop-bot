from __future__ import annotations

import httpx

from apps.notifier.errors import TelegramDeliveryError


class HttpTelegramTransport:
    """HTTP-транспорт для отправки сообщений через Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 10.0) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout

    def send(self, text: str) -> None:
        try:
            resp = httpx.post(
                self._url,
                json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TelegramDeliveryError(
                f"Не удалось отправить сообщение в Telegram: {exc}"
            ) from exc
