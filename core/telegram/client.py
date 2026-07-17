# -*- coding: utf-8 -*-
"""Async-клиент Telegram Bot API: Rich Messages, отправка и long polling."""

from __future__ import annotations

import asyncio
import html
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# INFO-логи httpx/httpcore содержат полный URL Telegram Bot API вместе с токеном.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Лимиты Telegram: legacy sendMessage и Rich Messages (Bot API 10.1+).
_TG_MESSAGE_LIMIT = 4096
_TG_RICH_MESSAGE_LIMIT = 32768

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
    # Rich Messages (Bot API 10.1+).
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "footer",
    "ul",
    "ol",
    "li",
    "table",
    "tr",
    "th",
    "td",
    "details",
    "summary",
    "aside",
    "cite",
    "mark",
    "sub",
    "sup",
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


_BLOCK_OPEN_RE = re.compile(
    r"<\s*(h[1-6]|p|pre|footer|ul|ol|table|blockquote|aside|details)\b",
    re.IGNORECASE,
)
_FULL_BOLD_LINE_RE = re.compile(
    r"^\s*(?:[^<\n]{0,12}\s*)?<(?:b|strong)>.+</(?:b|strong)>\s*$",
    re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _plain_html_text(value: str) -> str:
    return html.unescape(_TAG_STRIP_RE.sub("", value)).strip()


def _looks_like_title(line: str, *, first_content_line: bool) -> bool:
    """Эвристика миграции старых карточек в реальные rich-heading блоки."""
    if _BLOCK_OPEN_RE.match(line.strip()):
        return False
    plain = _plain_html_text(line)
    if not plain or len(plain) > 160:
        return False
    if _FULL_BOLD_LINE_RE.match(line):
        return True
    if not first_content_line:
        return False
    first = plain[0]
    starts_with_symbol = not first.isalnum() and ord(first) >= 0x2300
    return starts_with_symbol or bool(re.search(r"<(?:b|strong)>", line, re.IGNORECASE))


def _upgrade_html_to_rich(text: str) -> str:
    """Поднимает старый HTML в Rich HTML, не меняя смысл сообщения.

    Старые карточки уже отделяли заголовки первой строкой и жирными строками
    секций. Превращаем их в настоящие h2/h4; таблицы/details, которые уже
    сгенерированы новыми рендерами, оставляем как есть.
    """
    if not text:
        return text

    lines = text.splitlines()
    upgraded: list[str] = []
    first_content_seen = False
    protected_depth = 0
    protected_tags = ("pre", "blockquote", "table", "details", "aside")

    for line in lines:
        stripped = line.strip()
        first_content_line = bool(stripped) and not first_content_seen
        if (
            stripped
            and protected_depth == 0
            and _looks_like_title(line, first_content_line=first_content_line)
        ):
            level = 2 if first_content_line else 4
            upgraded.append(f"<h{level}>{stripped}</h{level}>")
        else:
            upgraded.append(line)

        if stripped:
            first_content_seen = True
        lowered = line.lower()
        for tag in protected_tags:
            protected_depth += len(re.findall(rf"<{tag}\b", lowered))
            protected_depth -= len(re.findall(rf"</{tag}\s*>", lowered))
        protected_depth = max(0, protected_depth)

    return "\n".join(upgraded)


def _rich_html_to_legacy(text: str) -> str:
    """Деградирует Rich HTML до поддерживаемого sendMessage HTML.

    Нужен для старого self-hosted Bot API и как страховка от ошибок rich parser.
    """
    legacy = text
    legacy = re.sub(r"<h[1-6]>(.*?)</h[1-6]>", r"<b>\1</b>\n", legacy, flags=re.I | re.S)
    legacy = re.sub(r"<footer>(.*?)</footer>", r"<i>\1</i>", legacy, flags=re.I | re.S)
    legacy = re.sub(r"<hr\s*/?>", "\n────────\n", legacy, flags=re.I)
    legacy = re.sub(r"<br\s*/?>", "\n", legacy, flags=re.I)
    legacy = re.sub(r"<summary>(.*?)</summary>", r"<b>\1</b>\n", legacy, flags=re.I | re.S)
    legacy = re.sub(r"</?(?:details|p|aside|cite)\b[^>]*>", "", legacy, flags=re.I)
    legacy = re.sub(r"<li\b[^>]*>", "• ", legacy, flags=re.I)
    legacy = re.sub(r"</li\s*>", "\n", legacy, flags=re.I)
    legacy = re.sub(r"</?(?:ul|ol)\b[^>]*>", "", legacy, flags=re.I)
    legacy = re.sub(r"<(?:th|td)\b[^>]*>", "", legacy, flags=re.I)
    legacy = re.sub(r"</(?:th|td)\s*>", "  ", legacy, flags=re.I)
    legacy = re.sub(r"<tr\b[^>]*>", "", legacy, flags=re.I)
    legacy = re.sub(r"</tr\s*>", "\n", legacy, flags=re.I)
    legacy = re.sub(r"</?table\b[^>]*>", "", legacy, flags=re.I)
    legacy = re.sub(r"<(?:mark)>(.*?)</(?:mark)>", r"<b>\1</b>", legacy, flags=re.I | re.S)
    legacy = re.sub(r"</?(?:sub|sup)\b[^>]*>", "", legacy, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", legacy).strip()


def _truncate_rich_html(text: str) -> str:
    return _truncate_message(text, limit=_TG_RICH_MESSAGE_LIMIT)


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


def _should_fallback_rich(exc: TelegramAPIError) -> bool:
    """Fallback только при несовместимости rich API/разметки, не при chat/ACL ошибках."""
    if exc.error_code == 404:
        return True
    if exc.error_code != 400:
        return False
    description = (exc.description or "").lower()
    return any(
        marker in description
        for marker in (
            "rich message",
            "rich_message",
            "can't parse",
            "cannot parse",
            "unsupported tag",
            "method not found",
        )
    )


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
        rich: bool = True,
    ) -> dict:
        """Отправляет сообщение, по умолчанию через Bot API Rich Messages.

        HTML автоматически получает настоящие заголовки для старых карточек.
        Markdown отправляется как Rich Markdown. При 400/404 от rich-метода
        выполняется безопасный fallback на legacy sendMessage.
        reply_to_message_id: ответ «реплаем» (AI-комментарий под алертом);
        allow_sending_without_reply — если оригинал удалён, шлём обычным сообщением.
        """
        normalized_mode = (parse_mode or "").strip().lower()
        rich_message: dict | None = None
        if rich and normalized_mode == "html":
            rich_message = {"html": _truncate_rich_html(_upgrade_html_to_rich(text))}
        elif rich and normalized_mode in {"markdown", "markdownv2"}:
            rich_message = {"markdown": _truncate_message(text, limit=_TG_RICH_MESSAGE_LIMIT)}

        if rich_message is not None:
            rich_payload: dict = {
                "chat_id": chat_id,
                "rich_message": rich_message,
            }
            if message_thread_id is not None:
                rich_payload["message_thread_id"] = message_thread_id
            if reply_markup:
                rich_payload["reply_markup"] = reply_markup
            if reply_to_message_id is not None:
                rich_payload["reply_parameters"] = {
                    "message_id": reply_to_message_id,
                    "allow_sending_without_reply": True,
                }
            try:
                data = await self._post_json("sendRichMessage", payload=rich_payload)
                return dict(data["result"])
            except TelegramAPIError as exc:
                if not _should_fallback_rich(exc):
                    raise
                logger.warning(
                    "sendRichMessage недоступен/отклонил разметку; fallback на sendMessage: %s",
                    exc.description,
                )

        legacy_text = text
        if normalized_mode == "html":
            legacy_text = _rich_html_to_legacy(text)
        legacy_text = _truncate_message(legacy_text)
        payload: dict = {
            "chat_id": chat_id,
            "text": legacy_text,
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
        """Редактирует rich-сообщение; при несовместимости откатывается на HTML."""
        rich_payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "rich_message": {"html": _truncate_rich_html(_upgrade_html_to_rich(text))},
        }
        if message_thread_id is not None:
            rich_payload["message_thread_id"] = message_thread_id
        if reply_markup:
            rich_payload["reply_markup"] = reply_markup
        try:
            await self._post_json("editMessageText", payload=rich_payload)
            return
        except TelegramAPIError as exc:
            if not _should_fallback_rich(exc):
                raise
            logger.warning(
                "rich edit отклонён; fallback на legacy editMessageText: %s",
                exc.description,
            )

        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": _truncate_message(_rich_html_to_legacy(text)),
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
