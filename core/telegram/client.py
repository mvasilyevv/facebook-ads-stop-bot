# -*- coding: utf-8 -*-
"""Async-клиент Telegram Bot API: отправка, редактирование, long polling."""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# INFO-логи httpx/httpcore содержат полный URL Telegram Bot API вместе с токеном.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Лимит Telegram на длину сообщения
_TG_MESSAGE_LIMIT = 4096

# Теги HTML mode Telegram, у которых есть парная закрывашка.
# Остальные ( <br>, <hr>, void-теги) Telegram HTML не поддерживает, игнорируем.
_TG_HTML_TAGS = (
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "code",
    "pre",
    "a",
    "blockquote",
    "tg-spoiler",
    "span",
)

# Регекс матчит открывающие и закрывающие теги, регистронезависимо.
# Имя тега захватывается без учёта атрибутов.
_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*>")


def _balance_html_tags(text: str) -> str:
    """Закрывает незакрытые HTML-теги Telegram в обрезанном тексте.

    Простой линейный проход: считаем стек открытых тегов; на каждый </tag> ищем
    ближайший подходящий открытый тег и снимаем его (с учётом возможной
    рассинхронизации из-за обрезки). В конце дописываем закрывающие теги для
    оставшихся в стеке элементов в обратном порядке.
    """
    if "<" not in text:
        return text

    stack: list[str] = []
    for match in _TAG_RE.finditer(text):
        is_closing = match.group(1) == "/"
        name = match.group(2).lower()
        if name not in _TG_HTML_TAGS:
            continue
        if is_closing:
            # Снимаем ближайший подходящий открытый тег (если есть).
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == name:
                    del stack[i]
                    break
        else:
            stack.append(name)

    if not stack:
        return text

    closing = "".join(f"</{name}>" for name in reversed(stack))
    return text + closing


def _truncate_message(text: str, limit: int = _TG_MESSAGE_LIMIT) -> str:
    """Обрезает сообщение до лимита Telegram, балансируя HTML-теги.

    После грубой обрезки до limit могут остаться незакрытые `<b>`, `<code>` и
    т.п. — Telegram отклоняет такое сообщение с «can't parse entities».
    Дописываем закрывающие теги в обратном порядке. Также отрезаем «обрубок»
    открывающего тега в конце (например, «<cod»), который Telegram пытался бы
    интерпретировать как литерал.
    """
    if len(text) <= limit:
        return text
    suffix = "\n\n... (сообщение обрезано)"
    truncated = text[: limit - len(suffix)]
    # Если обрезка пришлась внутрь незавершённого `<...`, отбрасываем недописанное.
    last_lt = truncated.rfind("<")
    last_gt = truncated.rfind(">")
    if last_lt > last_gt:
        truncated = truncated[:last_lt]
    balanced = _balance_html_tags(truncated)
    return balanced + suffix


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
        details = ""
        if error_code:
            details = f" [code={error_code}]"
        if description:
            details = f"{details}: {description}"
        super().__init__(f"Ошибка Telegram API при вызове {method}{details}")


class TelegramBotClient:
    """Минимальный async-клиент для Telegram Bot API."""

    def __init__(self, bot_token: str, http_client: httpx.AsyncClient | None = None) -> None:
        if not bot_token.strip():
            raise RuntimeError("Не задан токен Telegram-бота")
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
        data = self._decode_response(method, resp)
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
        """HTTP POST с однократным retry при 429 и backoff-ретраем при 502/503/504."""
        url = f"{self._base}/{method}"
        try:
            resp = await self._http.post(url, json=payload, timeout=request_timeout)
            if resp.status_code != 429:
                # Ретрай при транзиентных ошибках шлюза (2 попытки с нарастающей паузой)
                if resp.status_code in (502, 503, 504):
                    for delay in (2.0, 5.0):
                        logger.warning(
                            "Транзиентная ошибка Telegram API %s при вызове %s, повтор через %s с",
                            resp.status_code,
                            method,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        try:
                            resp = await self._http.post(url, json=payload, timeout=request_timeout)
                        except httpx.HTTPError:
                            raise RuntimeError(
                                f"Не удалось выполнить запрос к Telegram API ({method})"
                            ) from None
                        if resp.status_code not in (502, 503, 504):
                            break
                return resp
        except httpx.HTTPError:
            raise RuntimeError(f"Не удалось выполнить запрос к Telegram API ({method})") from None

        # Обработка 429: ждём и повторяем один раз
        wait = self._parse_retry_after(resp)
        logger.warning("Лимит Telegram API (429) при вызове %s, ожидание %s с", method, wait)
        await asyncio.sleep(wait)

        try:
            resp = await self._http.post(url, json=payload, timeout=request_timeout)
        except httpx.HTTPError:
            raise RuntimeError(f"Не удалось выполнить запрос к Telegram API ({method})") from None
        return resp

    @staticmethod
    def _decode_response(method: str, resp: httpx.Response) -> dict:
        """Декодирует JSON Telegram API без утечки полного URL с токеном в traceback."""
        try:
            data = resp.json()
        except ValueError:
            if resp.is_error:
                raise RuntimeError(
                    f"Не удалось выполнить запрос к Telegram API ({method}): HTTP {resp.status_code}"
                ) from None
            raise RuntimeError(f"Telegram API вернул некорректный JSON ({method})") from None

        if not isinstance(data, dict):
            raise RuntimeError(f"Telegram API вернул неожиданный JSON ({method})")
        return data

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> float:
        """Извлекает Retry-After из ответа (cap 30s, default 5s)."""
        raw = resp.headers.get("Retry-After")
        if raw is None:
            try:
                data = resp.json()
                raw = (data.get("parameters") or {}).get("retry_after")
            except (AttributeError, ValueError, TypeError):
                raw = None
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
        parse_mode: str | None = "HTML",
        reply_to_message_id: int | None = None,
    ) -> dict:
        """Отправляет сообщение в чат. Обрезает текст до лимита Telegram.

        parse_mode: режим разметки Telegram (HTML/MarkdownV2). None — без разметки.
        Дефолт HTML сохраняет обратную совместимость со старыми вызовами.
        reply_to_message_id: ответ «реплаем» (AI-комментарий под алертом);
        allow_sending_without_reply — если оригинал удалён, шлём обычным сообщением.
        """
        text = _truncate_message(text)
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
            payload["allow_sending_without_reply"] = True
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

    async def send_chat_action(self, *, chat_id: str, action: str = "typing") -> None:
        """sendChatAction — индикатор «печатает…» на ~5 секунд. Best-effort UX."""
        await self._post_json("sendChatAction", payload={"chat_id": chat_id, "action": action})

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        """Отвечает на callback query (убирает часики)."""
        payload: dict = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        await self._post_json("answerCallbackQuery", payload=payload)

    async def edit_message_reply_markup(
        self,
        *,
        chat_id: str,
        message_id: int,
        reply_markup: dict | None = None,
    ) -> None:
        """Заменяет inline-кнопки у существующего сообщения."""
        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        await self._post_json("editMessageReplyMarkup", payload=payload)

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

    async def set_my_commands(
        self,
        commands: list[dict],
        *,
        scope: dict | None = None,
        language_code: str | None = None,
    ) -> None:
        """Регистрирует список команд бота через Bot API setMyCommands.
        commands: [{"command": "start", "description": "..."}, ...]
        scope: BotCommandScope (например, {"type": "all_group_chats"} или {"type": "all_private_chats"}).
            Если None — используется default scope.
        """
        payload: dict = {"commands": commands}
        if scope is not None:
            payload["scope"] = scope
        if language_code is not None:
            payload["language_code"] = language_code
        await self._post_json("setMyCommands", payload=payload)
        logger.info(
            "setMyCommands: %d команд зарегистрировано (scope=%s)",
            len(commands),
            (scope or {}).get("type", "default"),
        )

    async def set_chat_menu_button(
        self,
        *,
        web_app_url: str,
        button_text: str = "📱 Открыть",
        chat_id: int | None = None,
    ) -> None:
        """Ставит MenuButtonWebApp для default scope или конкретного private chat.

        web_app_url должен быть HTTPS.
        """
        if not web_app_url.startswith("https://"):
            raise ValueError("web_app_url должен быть HTTPS")
        payload: dict = {
            "menu_button": {
                "type": "web_app",
                "text": button_text,
                "web_app": {"url": web_app_url},
            }
        }
        if chat_id is not None:
            payload["chat_id"] = int(chat_id)
        await self._post_json(
            "setChatMenuButton",
            payload=payload,
        )
        logger.info(
            "setChatMenuButton: web_app_url=%s scope=%s",
            web_app_url,
            "private_chat" if chat_id is not None else "default",
        )

    async def close(self) -> None:
        """Закрывает внутренний HTTP-клиент, если он был создан внутри."""
        if self._owns_http_client:
            await self._http.aclose()
