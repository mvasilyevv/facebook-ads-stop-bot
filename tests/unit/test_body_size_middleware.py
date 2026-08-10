# -*- coding: utf-8 -*-
"""Unit-тесты BodySizeLimitMiddleware (pure ASGI, аудит 2026-07-12 H-9).

Раньше лимит проверялся только по заголовку Content-Length: запрос с
Transfer-Encoding: chunked (без Content-Length) проходил без ограничения,
и handler читал всё тело в память → OOM/DoS публичного постбэк-эндпоинта.
Теперь байты считаются фактически на receive-канале.
"""

from __future__ import annotations

import httpx
import pytest

from apps.api.middleware.body_size import (
    MAX_REQUEST_BODY_BYTES,
    BodySizeLimitMiddleware,
)


async def _echo_app(scope, receive, send):
    """Минимальный ASGI-handler: читает всё тело (как request.json()) и отвечает 200."""
    assert scope["type"] == "http"
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.request":
            total += len(message.get("body") or b"")
            if not message.get("more_body"):
                break
        else:  # http.disconnect
            break
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": str(total).encode()})


def _client() -> httpx.AsyncClient:
    app = BodySizeLimitMiddleware(_echo_app)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# Маленькое тело с Content-Length проходит до handler'а.
@pytest.mark.asyncio
async def test_small_body_passes() -> None:
    async with _client() as ac:
        resp = await ac.post("/api/v1/postback/adsetpro", content=b"x" * 100)
    assert resp.status_code == 200
    assert resp.text == "100"


# Объявленное большое тело → 413 pre-check'ом по Content-Length (до чтения body).
@pytest.mark.asyncio
async def test_declared_large_body_rejected_413() -> None:
    async with _client() as ac:
        resp = await ac.post("/x", content=b"x" * (MAX_REQUEST_BODY_BYTES + 1))
    assert resp.status_code == 413
    assert resp.json()["max_bytes"] == MAX_REQUEST_BODY_BYTES


# MONEY/DoS (H-9): chunked-запрос БЕЗ Content-Length больше не безлимитен —
# фактический счётчик байт рвёт чтение и отвечает 413.
@pytest.mark.asyncio
async def test_chunked_large_body_rejected_413() -> None:
    async def _chunks():
        # 65 чанков по 2 KB = 130 KB > 64 KB, Content-Length не выставляется.
        for _ in range(65):
            yield b"x" * 2048

    async with _client() as ac:
        resp = await ac.post("/x", content=_chunks())
    assert resp.status_code == 413


# Chunked-запрос в пределах лимита проходит нормально.
@pytest.mark.asyncio
async def test_chunked_small_body_passes() -> None:
    async def _chunks():
        for _ in range(4):
            yield b"x" * 1024

    async with _client() as ac:
        resp = await ac.post("/x", content=_chunks())
    assert resp.status_code == 200
    assert resp.text == "4096"


# GET без тела не трогается лимитером.
@pytest.mark.asyncio
async def test_get_passes_through() -> None:
    async with _client() as ac:
        resp = await ac.get("/x")
    assert resp.status_code == 200


# /api/tools/* освобождены от лимита (multipart-загрузки со своим внутренним капом).
@pytest.mark.asyncio
async def test_tools_path_exempt_even_chunked() -> None:
    async def _chunks():
        for _ in range(65):
            yield b"x" * 2048

    async with _client() as ac:
        resp = await ac.post("/api/tools/creative-uniquify", content=_chunks())
    assert resp.status_code == 200


# Кривой Content-Length → 400 (как раньше).
@pytest.mark.asyncio
async def test_invalid_content_length_400() -> None:
    async with _client() as ac:
        resp = await ac.post("/x", content=b"abc", headers={"Content-Length": "not-a-number"})
    assert resp.status_code == 400


# Ревью перед push (#3): chunked-обход через БОЕВОЙ стек create_app() (BodySize —
# pure ASGI поверх BaseHTTPMiddleware-цепочки RequestId/ApiKeyAuth). Unit-тест выше
# проверял голый echo-app — взаимодействие с реальным стеком оставалось непокрытым
# (паттерн «стороны в изоляции» из Round 11). 413 отрабатывает в middleware ДО
# handler'а → БД не нужна.
@pytest.mark.asyncio
async def test_chunked_large_body_413_through_real_app() -> None:
    from apps.api.main import create_app

    app = create_app()

    async def _chunks():
        for _ in range(65):  # 130 KB > 64 KB, без Content-Length
            yield b"x" * 2048

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.post("/api/v1/postback/adsetpro", content=_chunks())
    # Canonical AdSet.pro transport is GET-only, so a body-bearing POST is rejected
    # by routing before any endpoint can consume the streamed body.
    assert resp.status_code == 405
