# -*- coding: utf-8 -*-
"""Middleware с лимитом размера тела запроса (защита от DoS).

Два уровня защиты (аудит 2026-07-12, H-9):
1. Быстрый pre-check по Content-Length — отбивает честно объявленные большие
   тела 413-м ДО чтения body.
2. Фактический счётчик байт на receive-канале — закрывает обход через
   Transfer-Encoding: chunked (без Content-Length) и лживый Content-Length:
   раньше такой запрос проходил без ограничения, а request.json() читал всё
   тело в память → OOM/DoS публичного POST /api/v1/postback/adsetpro.

Лимит 64 KB подобран под публичный endpoint POST /api/v1/postback/adsetpro:
постбэк трекера — это маленький JSON, на порядки меньше лимита. Остальные
endpoints API тело не принимают, поэтому общий лимит на app-level безопасен.

Реализация — pure ASGI (не BaseHTTPMiddleware): нужен доступ к receive-каналу
для подсчёта фактических байт.

Исключение (H7b): /api/tools/* грузят multipart с медиа (creative-uniquify и
campaigns/upload — реальные файлы много больше 64 KB) — у них свой внутренний лимит
в хендлере (_MAX_TOTAL_UPLOAD_BYTES, стримовое чтение по чанкам). Для этих path
64KB-лимит не применяется, иначе multipart с картинками/видео отбивался бы 413 до
handler'а. Гейт доступа разный: tools.py — dev-only (require_dev_tools), а
campaigns/* — X-API-Key (ApiKeyAuthMiddleware); общий — собственный размерный лимит.
"""

from __future__ import annotations

from fastapi import HTTPException as FastAPIHTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BODY_BYTES = 64 * 1024

_BODYLESS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Path-префиксы, освобождённые от 64KB-лимита (large multipart: creative-uniquify,
# campaigns/upload). У каждого свой внутренний размерный лимит в хендлере.
_EXEMPT_PATH_PREFIXES = ("/api/tools/",)


class _BodyTooLargeError(FastAPIHTTPException):
    """Фактический размер тела превысил лимит во время чтения (chunked-путь).

    Подкласс ИМЕННО fastapi.HTTPException (ревью перед push, #3): FastAPI при
    чтении body оборачивает ошибки receive в `except Exception → 400 error
    parsing the body`, пробрасывая как есть только СВОЙ HTTPException
    (starlette'овский родитель под except не попадает — он ШИРЕ fastapi'шного).
    Внутри FastAPI-стека ответ станет честным 413 через штатный
    exception-handler; вне FastAPI (голый ASGI-app) исключение долетает до
    нашего __call__ → 413 из _reject_too_large.
    """

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="request body too large")


class BodySizeLimitMiddleware:
    """Отбивает запросы с телом больше лимита 413-м ответом (pure ASGI)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))
        if method in _BODYLESS_METHODS or any(path.startswith(p) for p in _EXEMPT_PATH_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Уровень 1: быстрый pre-check по объявленному Content-Length.
        raw = _header(scope, b"content-length")
        if raw is not None:
            try:
                size = int(raw)
            except ValueError:
                await _reject(
                    scope, receive, send, status=400, detail="invalid content-length header"
                )
                return
            if size > MAX_REQUEST_BODY_BYTES:
                await _reject_too_large(scope, receive, send)
                return

        # Уровень 2 (H-9): фактический счётчик байт — chunked и лживый Content-Length.
        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body") or b"")
                if received_bytes > MAX_REQUEST_BODY_BYTES:
                    raise _BodyTooLargeError()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLargeError:
            # Ответ ещё не начат (handler упал на чтении body) → честный 413.
            # Если start уже ушёл клиенту — перепослать нельзя, пробрасываем.
            if response_started:
                raise
            await _reject_too_large(scope, receive, send)


def _header(scope: Scope, name: bytes) -> bytes | None:
    """Значение заголовка из ASGI-scope (первое совпадение, lower-case имя)."""
    for key, value in scope.get("headers") or ():
        if key == name:
            return value
    return None


async def _reject(scope: Scope, receive: Receive, send: Send, *, status: int, detail: str) -> None:
    response = JSONResponse(status_code=status, content={"detail": detail})
    await response(scope, receive, send)


async def _reject_too_large(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        status_code=413,
        content={"detail": "request body too large", "max_bytes": MAX_REQUEST_BODY_BYTES},
    )
    await response(scope, receive, send)
