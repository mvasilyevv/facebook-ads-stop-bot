# -*- coding: utf-8 -*-
"""Async-клиент Telegram Bot API: отправка, редактирование, long polling."""

from __future__ import annotations

import httpx


class TelegramBotClient:
    """Минимальный async-клиент для Telegram Bot API."""

    def __init__(self, bot_token: str, http_client: httpx.AsyncClient | None = None) -> None:
        if not bot_token.strip():
            raise RuntimeError("Не задан Telegram bot token")
        self._base = f"https://api.telegram.org/bot{bot_token.strip()}"
        self._http = http_client or httpx.AsyncClient(timeout=30.0)

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: dict | None = None,
    ) -> dict:
        """Отправляет сообщение в чат."""
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = await self._http.post(f"{self._base}/sendMessage", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError("Telegram не принял сообщение")
        return dict(data["result"])

    async def edit_message(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
    ) -> None:
        """Редактирует текст существующего сообщения."""
        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = await self._http.post(f"{self._base}/editMessageText", json=payload)
        resp.raise_for_status()

    # Алиас для обратной совместимости
    edit_message_text = edit_message

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        """Отвечает на callback query (убирает часики)."""
        payload: dict = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        resp = await self._http.post(f"{self._base}/answerCallbackQuery", json=payload)
        resp.raise_for_status()

    async def get_updates(self, *, offset: int | None, timeout_seconds: int = 25) -> list[dict]:
        """Long polling для получения обновлений."""
        payload: dict = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        resp = await self._http.post(
            f"{self._base}/getUpdates",
            json=payload,
            timeout=timeout_seconds + 10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError("Telegram отказал в long polling")
        return list(data.get("result") or [])
