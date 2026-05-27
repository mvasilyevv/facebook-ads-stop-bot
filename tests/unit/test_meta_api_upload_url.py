# -*- coding: utf-8 -*-
"""Unit-тесты MediaUploader.upload_image_from_url.

Мокаем gRPC stub, проверяем:
- формирование UploadImageRequest (image_url непустой, file_bytes пустой)
- возврат image_hash из stub-ответа
- валидацию URL (пустой, без https://)
- передачу name=None как пустая строка
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.python_grpc.v1 import meta_api_pb2
from core.meta_api.errors import PermanentError, SessionUnavailableError
from core.meta_api.upload import MediaUploader


def _make_client(upload_image_response=None) -> MagicMock:
    """Фабрика мок-клиента с настроенным UploadImage stub."""
    client = MagicMock()
    client.session_id = "test-session"
    stub = MagicMock()
    stub.UploadImage = AsyncMock(return_value=upload_image_response)
    client._stub = stub
    return client


# Загрузка по валидному https URL → UploadImageRequest с image_url непустым и file_bytes пустым.
@pytest.mark.asyncio
async def test_upload_image_from_url_sends_correct_request() -> None:
    response = meta_api_pb2.UploadImageResponse(
        image_hash="deadbeefhash",
        ok=True,
        error="",
        url="https://cdn.fb.com/img",
        duration_ms=100,
    )
    client = _make_client(response)
    uploader = MediaUploader(client)

    await uploader.upload_image_from_url("act_123", "https://example.com/photo.jpg", name="hero")

    client._stub.UploadImage.assert_awaited_once()
    request: meta_api_pb2.UploadImageRequest = client._stub.UploadImage.call_args.args[0]
    assert request.image_url == "https://example.com/photo.jpg"
    # file_bytes должен быть пустым при URL-загрузке
    assert request.file_bytes == b""
    assert request.ad_account_id == "act_123"
    assert request.session_id == "test-session"
    assert request.name == "hero"


# Stub возвращает image_hash → метод возвращает именно этот hash.
@pytest.mark.asyncio
async def test_upload_image_from_url_returns_image_hash() -> None:
    expected_hash = "aabbcc112233"
    response = meta_api_pb2.UploadImageResponse(
        image_hash=expected_hash,
        ok=True,
        error="",
        duration_ms=80,
    )
    client = _make_client(response)
    uploader = MediaUploader(client)

    result = await uploader.upload_image_from_url("act_456", "https://cdn.example.com/img.png")

    assert result == expected_hash


# Пустой URL → ValueError до вызова stub.
@pytest.mark.asyncio
async def test_upload_image_from_url_empty_url_raises_value_error() -> None:
    client = _make_client()
    uploader = MediaUploader(client)

    with pytest.raises(ValueError, match="пустым"):
        await uploader.upload_image_from_url("act_123", "")

    client._stub.UploadImage.assert_not_awaited()


# URL без https:// (http:// или ftp://) → ValueError до вызова stub.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://example.com/photo.jpg",
        "ftp://files.example.com/img.png",
        "//example.com/photo.jpg",
        "example.com/photo.jpg",
    ],
)
async def test_upload_image_from_url_non_https_raises_value_error(bad_url: str) -> None:
    client = _make_client()
    uploader = MediaUploader(client)

    with pytest.raises(ValueError, match="https://"):
        await uploader.upload_image_from_url("act_123", bad_url)

    client._stub.UploadImage.assert_not_awaited()


# name=None → в UploadImageRequest передаётся пустая строка "".
@pytest.mark.asyncio
async def test_upload_image_from_url_none_name_sends_empty_string() -> None:
    response = meta_api_pb2.UploadImageResponse(
        image_hash="abc",
        ok=True,
        error="",
        duration_ms=50,
    )
    client = _make_client(response)
    uploader = MediaUploader(client)

    await uploader.upload_image_from_url("act_123", "https://example.com/img.jpg", name=None)

    request: meta_api_pb2.UploadImageRequest = client._stub.UploadImage.call_args.args[0]
    assert request.name == ""


# ok=False с TOKEN_NOT_FOUND → SessionUnavailableError.
@pytest.mark.asyncio
async def test_upload_image_from_url_session_unavailable_on_token_missing() -> None:
    response = meta_api_pb2.UploadImageResponse(
        image_hash="",
        ok=False,
        error="TOKEN_NOT_FOUND_IN_PAGE",
        duration_ms=10,
    )
    client = _make_client(response)
    uploader = MediaUploader(client)

    with pytest.raises(SessionUnavailableError):
        await uploader.upload_image_from_url("act_123", "https://example.com/img.jpg")


# ok=False с Graph-ошибкой → PermanentError.
@pytest.mark.asyncio
async def test_upload_image_from_url_permanent_error_on_graph_failure() -> None:
    response = meta_api_pb2.UploadImageResponse(
        image_hash="",
        ok=False,
        error="GRAPH_ERROR_200: insufficient permissions",
        duration_ms=20,
    )
    client = _make_client(response)
    uploader = MediaUploader(client)

    with pytest.raises(PermanentError, match="insufficient permissions"):
        await uploader.upload_image_from_url("act_123", "https://example.com/img.jpg")
