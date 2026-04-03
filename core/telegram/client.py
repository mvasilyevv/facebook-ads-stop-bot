# -*- coding: utf-8 -*-
"""Async-клиент Telegram Bot API: отправка, редактирование, long polling."""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# Лимит Telegram на длину сообщения
_TG_MESSAGE_LIMIT = 4096


def _truncate_message(text: str, limit: int = _TG_MESSAGE_LIMIT) -> str:
    """Обрезает сообщение до лимита Telegram, добавляя маркер обрезки."""
    if len(text) <= limit:
        return text
    suffix = "\n\n... (сообщение обрезано)"
    return text[: limit - len(suffix)] + suffix


class TelegramAPIError(RuntimeError):
    """Ошибка вызова Telegram Bot API."""

    def __init__(
        self,
        *,
        method: str,
        description: str = "",
        error_code: int | None = None,
    ) -> None:
        self.method = method
        self.description = description
        self.error_code = error_code
        super().__init__(f"Ошибка Telegram API при вызове {method}")


class TelegramBotClient:
    """Минимальный async-клиент для Telegram Bot API."""

    def __init__(self, bot_token: str, http_client: httpx.AsyncClient | None = None) -> None:
        if not bot_token.strip():
            raise RuntimeError("Не задан Telegram bot token")
        self._base = f"https://api.telegram.org/bot{bot_token.strip()}"
        self._http = http_client or httpx.AsyncClient(timeout=30.0)
        self._owns_http_client = http_client is None

    async def _post_json(
        self,
        method: str,
        *,
        payload: dict,
        request_timeout: float | None = None,
    ) -> dict:
        """Выполняет POST-запрос к Telegram API и валидирует ответ.

        При HTTP 429 ждёт Retry-After (max 30s) и повторяет один раз.
        """
        resp = await self._do_request(method, payload=payload, request_timeout=request_timeout)
        data = resp.json()
        if not data.get("ok"):
            raise TelegramAPIError(
                method=method,
                description=str(data.get("description") or ""),
                error_code=int(data.get("error_code") or 0) or None,
            )
        return dict(data)

    async def _do_request(
        self,
        method: str,
        *,
        payload: dict,
        request_timeout: float | None = None,
    ) -> httpx.Response:
        """HTTP POST с однократным retry при 429."""
        try:
            resp = await self._http.post(
                f"{self._base}/{method}",
                json=payload,
                timeout=request_timeout,
            )
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Не удалось выполнить запрос к Telegram API ({method})") from exc

        # Обработка 429: ждём и повторяем один раз
        wait = self._parse_retry_after(resp)
        logger.warning("Telegram API rate limit (429) при вызове %s, ожидание %ss", method, wait)
        await asyncio.sleep(wait)

        try:
            resp = await self._http.post(
                f"{self._base}/{method}",
                json=payload,
                timeout=request_timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Не удалось выполнить запрос к Telegram API ({method})") from exc
        return resp

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> float:
        """Извлекает Retry-After из ответа (cap 30s, default 5s)."""
        raw = resp.headers.get("Retry-After")
        if raw is None:
            return 5.0
        try:
            return min(float(raw), 30.0)
        except (ValueError, TypeError):
            return 5.0

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        message_thread_id: int | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        """Отправляет сообщение в чат. Обрезает текст до лимита Telegram."""
        text = _truncate_message(text)
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if reply_markup:
            payload["reply_markup"] = reply_markup
        data = await self._post_json("sendMessage", payload=payload)
        return dict(data["result"])

    async def edit_message(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        message_thread_id: int | None = None,
        reply_markup: dict | None = None,
    ) -> None:
        """Редактирует текст существующего сообщения. Обрезает до лимита."""
        text = _truncate_message(text)
        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await self._post_json("editMessageText", payload=payload)

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        """Отвечает на callback query (убирает часики)."""
        payload: dict = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        await self._post_json("answerCallbackQuery", payload=payload)

    async def get_updates(self, *, offset: int | None, timeout_seconds: int = 25) -> list[dict]:
        """Long polling для получения обновлений."""
        payload: dict = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        data = await self._post_json(
            "getUpdates",
            payload=payload,
            request_timeout=timeout_seconds + 10,
        )
        return list(data.get("result") or [])

    async def get_chat(self, *, chat_id: str) -> dict:
        """Возвращает информацию о чате."""
        data = await self._post_json("getChat", payload={"chat_id": chat_id})
        return dict(data["result"])

    async def create_forum_topic(
        self,
        *,
        chat_id: str,
        name: str,
        icon_color: int | None = None,
    ) -> dict:
        """Создаёт forum topic в supergroup."""
        payload: dict = {
            "chat_id": chat_id,
            "name": name,
        }
        if icon_color is not None:
            payload["icon_color"] = icon_color
        data = await self._post_json("createForumTopic", payload=payload)
        return dict(data["result"])

    async def close(self) -> None:
        """Закрывает внутренний HTTP-клиент, если он был создан внутри."""
        if self._owns_http_client:
            await self._http.aclose()
