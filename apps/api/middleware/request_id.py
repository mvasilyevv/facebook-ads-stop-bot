# -*- coding: utf-8 -*-
"""Middleware, прокидывающий X-Request-Id для трассировки запросов.

Если клиент прислал заголовок — используем его (полезно при цепочках вызовов
через несколько сервисов). Если нет — генерируем uuid4.

Идентификатор:
- кладётся в request.state.request_id (доступен из роутеров)
- возвращается в response header X-Request-Id (клиент видит идентификатор)
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_HEADER_NAME = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Проставляет X-Request-Id на каждом запросе/ответе."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get(_HEADER_NAME) or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[_HEADER_NAME] = request_id
        return response
