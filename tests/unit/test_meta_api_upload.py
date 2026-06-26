# -*- coding: utf-8 -*-
"""Unit-тесты MediaUploader.

Покрываем:
- upload_image: вызывает UploadImage stub, возвращает image_hash.
- upload_image: валидация (act_ префикс, пустой bytes, размер > 8MB).
- upload_image: ошибка ok=False с TOKEN_NOT_FOUND → SessionUnavailableError.
- upload_image: ok=False с другим текстом → PermanentError.
- upload_video_from_bytes: chunked stream — корректное разбиение на чанки.
- upload_video: ValueError если файла нет / пустой.
- _video_chunks: первый чанк содержит метаданные, остальные — нет.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.python_grpc.v1 import meta_api_pb2
from core.meta_api.errors import PermanentError, SessionUnavailableError
from core.meta_api.upload import (
    DEFAULT_VIDEO_CHUNK_SIZE,
    MAX_IMAGE_SIZE_BYTES,
    MediaUploader,
)


def _make_client(stub_response_upload_image=None, stub_response_upload_video=None) -> MagicMock:
    """Фабрика мок-клиента: client._stub.UploadImage и UploadVideo."""
    client = MagicMock()
    client.session_id = "test-session"
    stub = MagicMock()
    stub.UploadImage = AsyncMock(return_value=stub_response_upload_image)
    stub.UploadVideo = AsyncMock(return_value=stub_response_upload_video)
    client._stub = stub
    return client


# upload_image: успешный ответ → возвращает image_hash.
@pytest.mark.asyncio
async def test_upload_image_returns_hash() -> None:
    response = meta_api_pb2.UploadImageResponse(
        image_hash="abc123hash",
        ok=True,
        error="",
        url="https://cdn.fb.com/abc",
        duration_ms=120,
    )
    client = _make_client(stub_response_upload_image=response)
    uploader = MediaUploader(client)

    result = await uploader.upload_image("act_123", b"binary-image-data")

    assert result == "abc123hash"
    client._stub.UploadImage.assert_awaited_once()
    call_args = client._stub.UploadImage.call_args
    request = call_args.args[0]
    assert request.ad_account_id == "act_123"
    assert request.file_bytes == b"binary-image-data"
    assert request.session_id == "test-session"


# upload_image: ad_account_id без act_ префикса → ValueError.
@pytest.mark.asyncio
async def test_upload_image_rejects_bad_account_id() -> None:
    client = _make_client()
    uploader = MediaUploader(client)
    with pytest.raises(ValueError, match="act_"):
        await uploader.upload_image("123", b"data")
    client._stub.UploadImage.assert_not_awaited()


# upload_image: пустой bytes → ValueError.
@pytest.mark.asyncio
async def test_upload_image_rejects_empty_bytes() -> None:
    client = _make_client()
    uploader = MediaUploader(client)
    with pytest.raises(ValueError, match="пустой"):
        await uploader.upload_image("act_123", b"")
    client._stub.UploadImage.assert_not_awaited()


# upload_image: размер > MAX → ValueError.
@pytest.mark.asyncio
async def test_upload_image_rejects_oversize() -> None:
    client = _make_client()
    uploader = MediaUploader(client)
    big_bytes = b"\x00" * (MAX_IMAGE_SIZE_BYTES + 1)
    with pytest.raises(ValueError, match="слишком большой"):
        await uploader.upload_image("act_123", big_bytes)


# upload_image: ok=False с TOKEN_NOT_FOUND → SessionUnavailableError.
@pytest.mark.asyncio
async def test_upload_image_session_unavailable_on_token_missing() -> None:
    response = meta_api_pb2.UploadImageResponse(
        image_hash="",
        ok=False,
        error="TOKEN_NOT_FOUND_IN_PAGE",
    )
    client = _make_client(stub_response_upload_image=response)
    uploader = MediaUploader(client)

    with pytest.raises(SessionUnavailableError):
        await uploader.upload_image("act_123", b"data")


# upload_image: ok=False с Graph error → PermanentError.
@pytest.mark.asyncio
async def test_upload_image_permanent_error_on_graph_failure() -> None:
    response = meta_api_pb2.UploadImageResponse(
        image_hash="",
        ok=False,
        error="GRAPH_ERROR_200: insufficient permissions",
    )
    client = _make_client(stub_response_upload_image=response)
    uploader = MediaUploader(client)

    with pytest.raises(PermanentError, match="insufficient permissions"):
        await uploader.upload_image("act_123", b"data")


# upload_image: ok=True но пустой hash → PermanentError.
@pytest.mark.asyncio
async def test_upload_image_empty_hash_on_success_is_permanent() -> None:
    response = meta_api_pb2.UploadImageResponse(
        image_hash="",
        ok=True,
        error="",
    )
    client = _make_client(stub_response_upload_image=response)
    uploader = MediaUploader(client)

    with pytest.raises(PermanentError, match="image_hash пустой"):
        await uploader.upload_image("act_123", b"data")


# upload_video_from_bytes: маленький файл → один чанк.
@pytest.mark.asyncio
async def test_upload_video_small_file_one_chunk() -> None:
    response = meta_api_pb2.UploadVideoResponse(
        video_id="999888",
        ok=True,
        error="",
        chunks_processed=1,
        duration_ms=500,
    )
    captured_chunks: list[meta_api_pb2.UploadVideoChunk] = []

    async def fake_upload_video(chunk_iterator, timeout=None):  # noqa: ASYNC109
        async for chunk in chunk_iterator:
            captured_chunks.append(chunk)
        return response

    client = MagicMock()
    client.session_id = "sess1"
    client._stub = MagicMock()
    client._stub.UploadVideo = fake_upload_video

    uploader = MediaUploader(client, chunk_size=1024)

    small_data = b"\x01" * 500  # < chunk_size
    video_id = await uploader.upload_video_from_bytes("act_777", small_data)

    assert video_id == "999888"
    assert len(captured_chunks) == 1
    first = captured_chunks[0]
    assert first.ad_account_id == "act_777"
    assert first.file_size == 500
    assert first.session_id == "sess1"
    assert first.is_last_chunk is True
    assert first.chunk_index == 0
    assert first.chunk_bytes == small_data


# upload_video_from_bytes: большой файл → несколько чанков, только первый с метаданными.
@pytest.mark.asyncio
async def test_upload_video_big_file_multiple_chunks() -> None:
    response = meta_api_pb2.UploadVideoResponse(
        video_id="555",
        ok=True,
        error="",
        chunks_processed=3,
        duration_ms=1500,
    )
    captured_chunks: list[meta_api_pb2.UploadVideoChunk] = []

    async def fake_upload_video(chunk_iterator, timeout=None):  # noqa: ASYNC109
        async for chunk in chunk_iterator:
            captured_chunks.append(chunk)
        return response

    client = MagicMock()
    client.session_id = "sess2"
    client._stub = MagicMock()
    client._stub.UploadVideo = fake_upload_video

    chunk_size = 100
    uploader = MediaUploader(client, chunk_size=chunk_size)
    data = b"X" * 250  # 250 байт = 3 chunks (100+100+50)

    video_id = await uploader.upload_video_from_bytes("act_777", data, filename="hero.mp4")

    assert video_id == "555"
    assert len(captured_chunks) == 3

    # Первый чанк — с метаданными.
    assert captured_chunks[0].ad_account_id == "act_777"
    assert captured_chunks[0].filename == "hero.mp4"
    assert captured_chunks[0].file_size == 250
    assert captured_chunks[0].chunk_index == 0
    assert captured_chunks[0].is_last_chunk is False
    assert len(captured_chunks[0].chunk_bytes) == 100

    # Второй — без метаданных.
    assert captured_chunks[1].ad_account_id == ""
    assert captured_chunks[1].chunk_index == 1
    assert captured_chunks[1].is_last_chunk is False
    assert len(captured_chunks[1].chunk_bytes) == 100

    # Третий — last_chunk.
    assert captured_chunks[2].chunk_index == 2
    assert captured_chunks[2].is_last_chunk is True
    assert len(captured_chunks[2].chunk_bytes) == 50


# upload_video: несуществующий путь → ValueError.
@pytest.mark.asyncio
async def test_upload_video_rejects_nonexistent_path(tmp_path: Path) -> None:
    client = _make_client()
    uploader = MediaUploader(client)
    missing = tmp_path / "does-not-exist.mp4"
    with pytest.raises(ValueError, match="не найден"):
        await uploader.upload_video("act_123", missing)


# upload_video: пустой файл → ValueError.
@pytest.mark.asyncio
async def test_upload_video_rejects_empty_file(tmp_path: Path) -> None:
    client = _make_client()
    uploader = MediaUploader(client)
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")
    with pytest.raises(ValueError, match="пустой"):
        await uploader.upload_video("act_123", empty_file)


# upload_video: реальный файл → чанки идут в stub.
@pytest.mark.asyncio
async def test_upload_video_real_file_chunks(tmp_path: Path) -> None:
    video_file = tmp_path / "test.mp4"
    payload = b"abcde" * 50  # 250 байт
    video_file.write_bytes(payload)

    response = meta_api_pb2.UploadVideoResponse(
        video_id="vid_999",
        ok=True,
        error="",
        chunks_processed=3,
    )
    captured: list[meta_api_pb2.UploadVideoChunk] = []

    async def fake_upload(chunk_iterator, timeout=None):  # noqa: ASYNC109
        async for chunk in chunk_iterator:
            captured.append(chunk)
        return response

    client = MagicMock()
    client.session_id = "s"
    client._stub = MagicMock()
    client._stub.UploadVideo = fake_upload

    uploader = MediaUploader(client, chunk_size=100)
    video_id = await uploader.upload_video("act_999", video_file)

    assert video_id == "vid_999"
    assert len(captured) == 3
    # Перепроверим reassembly: сумма chunk_bytes == исходные данные.
    reassembled = b"".join(c.chunk_bytes for c in captured)
    assert reassembled == payload


# upload_video: ok=False с TOKEN_NOT_FOUND → SessionUnavailableError.
@pytest.mark.asyncio
async def test_upload_video_session_unavailable(tmp_path: Path) -> None:
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"data")

    response = meta_api_pb2.UploadVideoResponse(
        video_id="",
        ok=False,
        error="TOKEN_NOT_FOUND_IN_PAGE",
    )

    async def fake_upload(chunk_iterator, timeout=None):  # noqa: ASYNC109
        async for _ in chunk_iterator:
            pass
        return response

    client = MagicMock()
    client.session_id = "s"
    client._stub = MagicMock()
    client._stub.UploadVideo = fake_upload

    uploader = MediaUploader(client)
    with pytest.raises(SessionUnavailableError):
        await uploader.upload_video("act_999", video_file)


# upload_video: ok=False с другой ошибкой → PermanentError.
@pytest.mark.asyncio
async def test_upload_video_permanent_error(tmp_path: Path) -> None:
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"data")

    response = meta_api_pb2.UploadVideoResponse(
        video_id="",
        ok=False,
        error="GRAPH_ERROR_400: invalid video format",
    )

    async def fake_upload(chunk_iterator, timeout=None):  # noqa: ASYNC109
        async for _ in chunk_iterator:
            pass
        return response

    client = MagicMock()
    client.session_id = "s"
    client._stub = MagicMock()
    client._stub.UploadVideo = fake_upload

    uploader = MediaUploader(client)
    with pytest.raises(PermanentError, match="invalid video format"):
        await uploader.upload_video("act_999", video_file)


# MediaUploader: chunk_size <= 0 → ValueError в конструкторе.
def test_media_uploader_rejects_invalid_chunk_size() -> None:
    client = MagicMock()
    with pytest.raises(ValueError, match="chunk_size"):
        MediaUploader(client, chunk_size=0)
    with pytest.raises(ValueError, match="chunk_size"):
        MediaUploader(client, chunk_size=-1)


# Дефолтный chunk_size = 3MB (запас под proto-метаданные под gRPC-лимит).
def test_media_uploader_default_chunk_size() -> None:
    client = MagicMock()
    uploader = MediaUploader(client)
    assert uploader._chunk_size == DEFAULT_VIDEO_CHUNK_SIZE == 3 * 1024 * 1024
    # Чанк + метаданные первого сообщения должны умещаться в дефолтный gRPC-лимит 4MB.
    assert DEFAULT_VIDEO_CHUNK_SIZE < 4 * 1024 * 1024


# wait_video_ready: поллит GET /{video_id}?fields=status до status=ready.
@pytest.mark.asyncio
async def test_wait_video_ready_polls_until_ready() -> None:
    client = MagicMock()
    client.execute_graph_call = AsyncMock(
        side_effect=[
            {"status": {"video_status": "processing"}},
            {"status": {"video_status": "ready"}},
        ]
    )
    uploader = MediaUploader(client)
    ok = await uploader.wait_video_ready("vid1", timeout=5, interval=0.001)
    assert ok is True
    assert client.execute_graph_call.await_count == 2


# wait_video_ready: status=error → PermanentError (видео не обработалось).
@pytest.mark.asyncio
async def test_wait_video_ready_raises_on_error() -> None:
    client = MagicMock()
    client.execute_graph_call = AsyncMock(return_value={"status": {"video_status": "error"}})
    uploader = MediaUploader(client)
    with pytest.raises(PermanentError):
        await uploader.wait_video_ready("vid1", timeout=5, interval=0.001)


# wait_video_ready: таймаут (всё ещё processing) → False, залив НЕ роняется.
@pytest.mark.asyncio
async def test_wait_video_ready_timeout_returns_false() -> None:
    client = MagicMock()
    client.execute_graph_call = AsyncMock(return_value={"status": {"video_status": "processing"}})
    uploader = MediaUploader(client)
    ok = await uploader.wait_video_ready("vid1", timeout=0.01, interval=0.005)
    assert ok is False


# wait_video_ready: ошибка чтения статуса проглатывается, поллинг продолжается до ready.
@pytest.mark.asyncio
async def test_wait_video_ready_swallows_read_errors() -> None:
    client = MagicMock()
    client.execute_graph_call = AsyncMock(
        side_effect=[RuntimeError("network blip"), {"status": {"video_status": "ready"}}]
    )
    uploader = MediaUploader(client)
    ok = await uploader.wait_video_ready("vid1", timeout=5, interval=0.001)
    assert ok is True
