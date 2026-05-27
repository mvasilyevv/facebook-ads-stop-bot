# -*- coding: utf-8 -*-
"""Middleware с лимитом размера тела запроса (защита от DoS).

Проверяет Content-Length до того, как handler начнёт читать body. Запросы без
тела (GET/HEAD/OPTIONS) пропускаются мгновенно.

Лимит 64 KB подобран под публичный endpoint POST /api/v1/postback/adsetpro:
постбэк трекера — это маленький JSON, на порядки меньше лимита. Остальные
endpoints API тело не принимают, поэтому общий лимит на app-level безопасен.
Если в будущем появится endpoint с большим body — поднять MAX_REQUEST_BODY_BYTES
или вынести middleware на конкретный path.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

MAX_REQUEST_BODY_BYTES = 64 * 1024

_BODYLESS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Отбивает запросы с Content-Length больше лимита 413-м ответом."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.method.upper() not in _BODYLESS_METHODS:
            raw = request.headers.get("content-length")
            if raw is not None:
                try:
                    size = int(raw)
                except ValueError:
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "invalid content-length header"},
                    )
                if size > MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": "request body too large",
                            "max_bytes": MAX_REQUEST_BODY_BYTES,
                        },
                    )
        return await call_next(request)
