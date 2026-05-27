# -*- coding: utf-8 -*-
"""MediaUploader — Python-обёртка над gRPC UploadImage/UploadVideo.

Картинка грузится одним сообщением (single-shot, до ~8MB реально).
Видео — client streaming: разбиваем файл на chunks нужного размера и
шлём по одному. Browser-agent держит state сессии загрузки между чанками.

Использование:
    client = MetaApiClient(host="localhost", port=50051)
    await client.start()
    uploader = MediaUploader(client)
    image_hash = await uploader.upload_image("act_123", image_bytes, filename="hero.jpg")
    video_id = await uploader.upload_video("act_123", Path("creative.mp4"))
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator

import grpc

from clients.python_grpc.v1 import meta_api_pb2
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
    MetaApiError,
    PermanentError,
    SessionUnavailableError,
    TemporaryError,
)

logger = logging.getLogger(__name__)

# Дефолтный размер чанка для видео-upload. 4MB — компромисс между:
# - меньше = больше round-trip'ов к Meta, выше latency
# - больше = риск превысить max gRPC message (50MB) с учётом proto overhead
DEFAULT_VIDEO_CHUNK_SIZE = 4 * 1024 * 1024

# Таймаут одного gRPC вызова на upload. 2 минуты — Meta может медленно обрабатывать.
_UPLOAD_TIMEOUT_SECONDS = 180.0

# Лимит размера картинки для single-shot upload. Meta документирует 8MB, но
# с учётом base64-кодирования и proto overhead закладываем запас.
MAX_IMAGE_SIZE_BYTES = 8 * 1024 * 1024


class MediaUploader:
    """Загрузка картинок/видео через Marketing API изнутри Vision-сессии.

    Картинка → image_hash, видео → video_id. Эти идентификаторы потом
    используются в AdCreative.object_story_spec при создании ad.
    """

    def __init__(
        self,
        client: MetaApiClient,
        *,
        chunk_size: int = DEFAULT_VIDEO_CHUNK_SIZE,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size должен быть > 0, получено {chunk_size}")
        self._client = client
        self._chunk_size = chunk_size

    # ====================== Image ======================

    async def upload_image(
        self,
        ad_account_id: str,
        image_bytes: bytes,
        *,
        filename: str = "upload.jpg",
        content_type: str = "image/jpeg",
    ) -> str:
        """Загрузить картинку → вернуть image_hash для использования в creative.

        Args:
            ad_account_id: с префиксом "act_".
            image_bytes: бинарные данные картинки.
            filename: имя файла (попадает в multipart filename).
            content_type: MIME-тип (image/jpeg, image/png, image/gif).

        Raises:
            ValueError: пустой/огромный файл, неправильный ad_account_id.
            SessionUnavailableError: browser-agent или Vision-сессия недоступны.
            PermanentError: Meta вернул ошибку (токен невалиден, права, и т.д.).
        """
        if self._client._stub is None:  # type: ignore[attr-defined]
            raise RuntimeError("MetaApiClient не запущен: вызови await client.start()")
        if not ad_account_id or not ad_account_id.startswith("act_"):
            raise ValueError(
                f"ad_account_id должен начинаться с 'act_', получено {ad_account_id!r}"
            )
        if not image_bytes:
            raise ValueError("image_bytes пустой")
        if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
            raise ValueError(
                f"image_bytes слишком большой: {len(image_bytes)} > {MAX_IMAGE_SIZE_BYTES} (MAX)"
            )

        request = meta_api_pb2.UploadImageRequest(
            session_id=self._client.session_id,
            ad_account_id=ad_account_id,
            filename=filename,
            content_type=content_type,
            file_bytes=image_bytes,
        )

        try:
            response = await self._client._stub.UploadImage(  # type: ignore[attr-defined]
                request,
                timeout=_UPLOAD_TIMEOUT_SECONDS,
            )
        except grpc.RpcError as exc:  # type: ignore[misc]
            raise self._grpc_to_error(exc, endpoint=f"/{ad_account_id}/adimages") from exc

        if not response.ok:
            err_msg = response.error or "UploadImage: неизвестная ошибка"
            # Различаем недоступность сессии vs ошибку Meta.
            if "TOKEN_NOT_FOUND" in err_msg or "PAGE_EVALUATE_ERROR" in err_msg:
                raise SessionUnavailableError(err_msg, endpoint=f"/{ad_account_id}/adimages")
            raise PermanentError(err_msg, endpoint=f"/{ad_account_id}/adimages")

        if not response.image_hash:
            raise PermanentError(
                "UploadImage: image_hash пустой в успешном ответе",
                endpoint=f"/{ad_account_id}/adimages",
            )

        logger.info(
            "upload_image: hash=%s url=%s duration=%dмс",
            response.image_hash[:16] + "...",
            response.url[:60] if response.url else "",
            response.duration_ms,
        )
        return response.image_hash

    async def upload_image_from_url(
        self,
        ad_account_id: str,
        image_url: str,
        name: str | None = None,
    ) -> str:
        """Загрузка картинки по URL — Meta сама скачает.

        Возвращает image_hash из ответа Graph API.

        Args:
            ad_account_id: с префиксом "act_".
            image_url: HTTPS-ссылка на картинку (только https).
            name: имя картинки в Meta (опционально).

        Raises:
            ValueError: пустой URL или URL без https://.
            SessionUnavailableError: browser-agent или Vision-сессия недоступны.
            PermanentError: Meta вернул ошибку (токен, права и т.д.).
        """
        if self._client._stub is None:  # type: ignore[attr-defined]
            raise RuntimeError("MetaApiClient не запущен: вызови await client.start()")
        if not ad_account_id or not ad_account_id.startswith("act_"):
            raise ValueError(
                f"ad_account_id должен начинаться с 'act_', получено {ad_account_id!r}"
            )
        if not image_url:
            raise ValueError("image_url не может быть пустым")
        if not image_url.startswith("https://"):
            raise ValueError(f"image_url должен начинаться с 'https://', получено {image_url!r}")

        request = meta_api_pb2.UploadImageRequest(
            session_id=self._client.session_id,
            ad_account_id=ad_account_id,
            image_url=image_url,
            name=name or "",
            # file_bytes/filename/content_type оставляем пустыми — URL-путь
        )

        try:
            response = await self._client._stub.UploadImage(  # type: ignore[attr-defined]
                request,
                timeout=_UPLOAD_TIMEOUT_SECONDS,
            )
        except grpc.RpcError as exc:  # type: ignore[misc]
            raise self._grpc_to_error(exc, endpoint=f"/{ad_account_id}/adimages") from exc

        if not response.ok:
            err_msg = response.error or "UploadImage (url): неизвестная ошибка"
            if "TOKEN_NOT_FOUND" in err_msg or "PAGE_EVALUATE_ERROR" in err_msg:
                raise SessionUnavailableError(err_msg, endpoint=f"/{ad_account_id}/adimages")
            raise PermanentError(err_msg, endpoint=f"/{ad_account_id}/adimages")

        if not response.image_hash:
            raise PermanentError(
                "UploadImage (url): image_hash пустой в успешном ответе",
                endpoint=f"/{ad_account_id}/adimages",
            )

        logger.info(
            "upload_image_from_url: hash=%s url=%s duration=%dмс",
            response.image_hash[:16] + "...",
            image_url[:60],
            response.duration_ms,
        )
        return response.image_hash

    # ====================== Video ======================

    async def upload_video(
        self,
        ad_account_id: str,
        video_path: Path | str,
        *,
        filename: str | None = None,
    ) -> str:
        """Загрузить видео chunked → вернуть video_id.

        Файл разбивается на chunk_size кусочки, каждый отправляется отдельным
        UploadVideoChunk сообщением. Первый чанк содержит метаданные (ad_account_id,
        filename, file_size). Последний — флаг is_last_chunk=true.

        Args:
            ad_account_id: с префиксом "act_".
            video_path: путь к файлу (Path или str).
            filename: явное имя файла (по умолчанию — basename из path).

        Raises:
            ValueError: пустой/несуществующий файл, кривой ad_account_id.
            SessionUnavailableError: browser-agent недоступен.
            PermanentError: ошибка Meta API.
        """
        if self._client._stub is None:  # type: ignore[attr-defined]
            raise RuntimeError("MetaApiClient не запущен: вызови await client.start()")
        if not ad_account_id or not ad_account_id.startswith("act_"):
            raise ValueError(
                f"ad_account_id должен начинаться с 'act_', получено {ad_account_id!r}"
            )

        path = Path(video_path)
        # Sync файловые операции внутри async: допустимо, потому что вызывается
        # 1 раз в начале upload-сессии, и сам upload потом блокируется на сети,
        # а не на диске. Альтернативу через asyncio.to_thread не используем
        # из-за сложности без выигрыша.
        if not path.exists() or not path.is_file():  # noqa: ASYNC240
            raise ValueError(f"video_path: файл не найден или не файл: {path}")
        file_size = path.stat().st_size  # noqa: ASYNC240
        if file_size == 0:
            raise ValueError(f"video_path: файл пустой: {path}")

        effective_filename = filename or path.name

        try:
            response = await self._client._stub.UploadVideo(  # type: ignore[attr-defined]
                self._video_chunks(
                    ad_account_id=ad_account_id,
                    filename=effective_filename,
                    file_size=file_size,
                    video_path=path,
                ),
                timeout=_UPLOAD_TIMEOUT_SECONDS,
            )
        except grpc.RpcError as exc:  # type: ignore[misc]
            raise self._grpc_to_error(exc, endpoint=f"/{ad_account_id}/advideos") from exc

        if not response.ok:
            err_msg = response.error or "UploadVideo: неизвестная ошибка"
            if "TOKEN_NOT_FOUND" in err_msg or "PAGE_EVALUATE_ERROR" in err_msg:
                raise SessionUnavailableError(err_msg, endpoint=f"/{ad_account_id}/advideos")
            raise PermanentError(err_msg, endpoint=f"/{ad_account_id}/advideos")

        if not response.video_id:
            raise PermanentError(
                "UploadVideo: video_id пустой в успешном ответе",
                endpoint=f"/{ad_account_id}/advideos",
            )

        logger.info(
            "upload_video: video_id=%s chunks=%d duration=%dмс file_size=%d",
            response.video_id,
            response.chunks_processed,
            response.duration_ms,
            file_size,
        )
        return response.video_id

    async def upload_video_from_bytes(
        self,
        ad_account_id: str,
        video_bytes: bytes,
        *,
        filename: str = "upload.mp4",
    ) -> str:
        """То же что upload_video, но из bytes (для случаев без файла на диске).

        Удобно для in-memory creative'ов и тестов.
        """
        if self._client._stub is None:  # type: ignore[attr-defined]
            raise RuntimeError("MetaApiClient не запущен: вызови await client.start()")
        if not ad_account_id or not ad_account_id.startswith("act_"):
            raise ValueError(
                f"ad_account_id должен начинаться с 'act_', получено {ad_account_id!r}"
            )
        if not video_bytes:
            raise ValueError("video_bytes пустой")

        file_size = len(video_bytes)

        try:
            response = await self._client._stub.UploadVideo(  # type: ignore[attr-defined]
                self._video_chunks_from_bytes(
                    ad_account_id=ad_account_id,
                    filename=filename,
                    file_size=file_size,
                    video_bytes=video_bytes,
                ),
                timeout=_UPLOAD_TIMEOUT_SECONDS,
            )
        except grpc.RpcError as exc:  # type: ignore[misc]
            raise self._grpc_to_error(exc, endpoint=f"/{ad_account_id}/advideos") from exc

        if not response.ok:
            err_msg = response.error or "UploadVideo: неизвестная ошибка"
            if "TOKEN_NOT_FOUND" in err_msg or "PAGE_EVALUATE_ERROR" in err_msg:
                raise SessionUnavailableError(err_msg, endpoint=f"/{ad_account_id}/advideos")
            raise PermanentError(err_msg, endpoint=f"/{ad_account_id}/advideos")

        if not response.video_id:
            raise PermanentError(
                "UploadVideo: video_id пустой",
                endpoint=f"/{ad_account_id}/advideos",
            )

        return response.video_id

    # ====================== внутреннее ======================

    async def _video_chunks(
        self,
        *,
        ad_account_id: str,
        filename: str,
        file_size: int,
        video_path: Path,
    ) -> AsyncIterator[meta_api_pb2.UploadVideoChunk]:
        """Async iterator: читает файл и эмитит UploadVideoChunk-сообщения.

        Sync open() здесь намеренно: чтение чанка с диска быстрее, чем его
        отправка по сети. Использовать aiofiles ради не-блокирующего read()
        даст лишь маргинальное улучшение в стандартном случае.
        """
        with open(video_path, "rb") as f:  # noqa: ASYNC230
            chunk_index = 0
            bytes_sent = 0
            while True:
                chunk_data = f.read(self._chunk_size)
                if not chunk_data:
                    break
                bytes_sent += len(chunk_data)
                is_last = bytes_sent >= file_size
                # В первом чанке заполняем все метаданные.
                if chunk_index == 0:
                    yield meta_api_pb2.UploadVideoChunk(
                        session_id=self._client.session_id,
                        ad_account_id=ad_account_id,
                        filename=filename,
                        file_size=file_size,
                        chunk_bytes=chunk_data,
                        chunk_index=chunk_index,
                        is_last_chunk=is_last,
                        is_init=False,
                    )
                else:
                    yield meta_api_pb2.UploadVideoChunk(
                        chunk_bytes=chunk_data,
                        chunk_index=chunk_index,
                        is_last_chunk=is_last,
                    )
                chunk_index += 1
                if is_last:
                    break

    async def _video_chunks_from_bytes(
        self,
        *,
        ad_account_id: str,
        filename: str,
        file_size: int,
        video_bytes: bytes,
    ) -> AsyncIterator[meta_api_pb2.UploadVideoChunk]:
        """Async iterator из in-memory bytes."""
        chunk_index = 0
        bytes_sent = 0
        view = memoryview(video_bytes)
        while bytes_sent < file_size:
            end = min(bytes_sent + self._chunk_size, file_size)
            chunk_data = bytes(view[bytes_sent:end])
            new_bytes_sent = end
            is_last = new_bytes_sent >= file_size
            if chunk_index == 0:
                yield meta_api_pb2.UploadVideoChunk(
                    session_id=self._client.session_id,
                    ad_account_id=ad_account_id,
                    filename=filename,
                    file_size=file_size,
                    chunk_bytes=chunk_data,
                    chunk_index=chunk_index,
                    is_last_chunk=is_last,
                    is_init=False,
                )
            else:
                yield meta_api_pb2.UploadVideoChunk(
                    chunk_bytes=chunk_data,
                    chunk_index=chunk_index,
                    is_last_chunk=is_last,
                )
            chunk_index += 1
            bytes_sent = new_bytes_sent

    @staticmethod
    def _grpc_to_error(exc: grpc.RpcError, *, endpoint: str) -> MetaApiError:
        """Преобразовать gRPC-ошибку upload-вызова в доменную."""
        code = exc.code() if hasattr(exc, "code") else None  # type: ignore[union-attr]
        details = exc.details() if hasattr(exc, "details") else str(exc)  # type: ignore[union-attr]

        if code == grpc.StatusCode.FAILED_PRECONDITION:
            return SessionUnavailableError(
                f"Vision-сессия не готова к upload: {details}",
                endpoint=endpoint,
            )
        if code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
            return TemporaryError(
                f"browser-agent недоступен ({code.name if code else '?'}): {details}",
                endpoint=endpoint,
            )
        return TemporaryError(
            f"gRPC error {code.name if code else '?'}: {details}",
            endpoint=endpoint,
        )
