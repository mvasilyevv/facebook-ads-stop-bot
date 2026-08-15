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

import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import AsyncIterator, NoReturn

import grpc

from clients.python_grpc.v1 import meta_api_pb2
from core.meta_api.client import MetaApiClient, media_operation_binding
from core.meta_api.errors import (
    AmbiguousResultError,
    BrowserReadinessRejectedError,
    MetaApiError,
    PermanentError,
    SessionUnavailableError,
)
from core.meta_api.identity import graph_ad_account_id, require_ad_account_id
from core.safe_diagnostics import safe_exception_diagnostic

logger = logging.getLogger(__name__)

# Дефолтный размер чанка для видео-upload. 3MB (не 4MB): первый чанк несёт ещё и
# метаданные (session_id/filename/file_size), и ровно 4MB chunk_bytes + proto overhead
# давали 4194354 > дефолтного gRPC-лимита 4194304 (RESOURCE_EXHAUSTED на /advideos).
# 3MB оставляет запас под метаданные даже на сервере с дефолтным 4MB-лимитом (сервер
# browser-agent поднят до 50MB, но чанк держим с запасом — defense-in-depth).
DEFAULT_VIDEO_CHUNK_SIZE = 3 * 1024 * 1024

# Таймаут одного gRPC вызова на upload. 2 минуты — Meta может медленно обрабатывать.
_UPLOAD_TIMEOUT_SECONDS = 180.0

# Ожидание готовности видео (status=ready) перед созданием AdCreative.
VIDEO_READY_TIMEOUT_SECONDS = 120.0
VIDEO_READY_POLL_INTERVAL_SECONDS = 4.0

# Лимит размера картинки для single-shot upload. Meta документирует 8MB, но
# с учётом base64-кодирования и proto overhead закладываем запас.
MAX_IMAGE_SIZE_BYTES = 8 * 1024 * 1024


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
            ad_account_id: явный числовой ID (форма ``act_123`` тоже принимается).
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
        account_id = require_ad_account_id(ad_account_id)
        endpoint = f"/{graph_ad_account_id(account_id)}/adimages"
        if not image_bytes:
            raise ValueError("image_bytes пустой")
        if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
            raise ValueError(
                f"image_bytes слишком большой: {len(image_bytes)} > {MAX_IMAGE_SIZE_BYTES} (MAX)"
            )

        authorization = await self._client.prepare_operation_authorization(
            rpc="upload_image",
            operation=media_operation_binding(
                rpc="upload_image",
                attributes={
                    "filename": filename,
                    "content_type": content_type,
                    "content_sha256": hashlib.sha256(image_bytes).hexdigest(),
                },
            ),
            ad_account_id=account_id,
        )
        request = meta_api_pb2.UploadImageRequest(
            ad_account_id=account_id,
            filename=filename,
            content_type=content_type,
            file_bytes=image_bytes,
            **authorization,
        )

        try:
            response = await self._client._stub.UploadImage(  # type: ignore[attr-defined]
                request,
                timeout=_UPLOAD_TIMEOUT_SECONDS,
            )
        except grpc.RpcError as exc:  # type: ignore[misc]
            await self._raise_grpc_error(exc, endpoint=endpoint)

        if not response.ok:
            err_msg = response.error or "UploadImage: неизвестная ошибка"
            raise self._upload_response_error(err_msg, endpoint=endpoint)

        if not response.image_hash:
            raise AmbiguousResultError(
                "UploadImage вернул ok=true без image_hash после dispatch",
                endpoint=endpoint,
            )

        logger.info(
            "upload_image: hash=%s url=%s duration=%dмс",
            response.image_hash[:16] + "...",
            response.url[:60] if response.url else "",
            response.duration_ms,
        )
        self._client._remember_campaign_uploaded_image_hash(
            response.image_hash,
            ad_account_id=account_id,
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
            ad_account_id: явный числовой ID (форма ``act_123`` тоже принимается).
            video_path: путь к файлу (Path или str).
            filename: явное имя файла (по умолчанию — basename из path).

        Raises:
            ValueError: пустой/несуществующий файл, кривой ad_account_id.
            SessionUnavailableError: browser-agent недоступен.
            PermanentError: ошибка Meta API.
        """
        if self._client._stub is None:  # type: ignore[attr-defined]
            raise RuntimeError("MetaApiClient не запущен: вызови await client.start()")
        account_id = require_ad_account_id(ad_account_id)
        endpoint = f"/{graph_ad_account_id(account_id)}/advideos"

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
        content_sha256 = await asyncio.to_thread(_file_sha256, path)

        try:
            response = await self._client._stub.UploadVideo(  # type: ignore[attr-defined]
                self._video_chunks(
                    ad_account_id=account_id,
                    filename=effective_filename,
                    file_size=file_size,
                    content_sha256=content_sha256,
                    video_path=path,
                ),
                timeout=_UPLOAD_TIMEOUT_SECONDS,
            )
        except grpc.RpcError as exc:  # type: ignore[misc]
            await self._raise_grpc_error(exc, endpoint=endpoint)

        if not response.ok:
            err_msg = response.error or "UploadVideo: неизвестная ошибка"
            raise self._upload_response_error(err_msg, endpoint=endpoint)

        if not response.video_id:
            raise AmbiguousResultError(
                "UploadVideo вернул ok=true без video_id после dispatch",
                endpoint=endpoint,
            )

        logger.info(
            "upload_video: video_id=%s chunks=%d duration=%dмс file_size=%d",
            response.video_id,
            response.chunks_processed,
            response.duration_ms,
            file_size,
        )
        self._client._remember_campaign_uploaded_video_id(
            response.video_id,
            ad_account_id=account_id,
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
        account_id = require_ad_account_id(ad_account_id)
        endpoint = f"/{graph_ad_account_id(account_id)}/advideos"
        if not video_bytes:
            raise ValueError("video_bytes пустой")

        file_size = len(video_bytes)
        content_sha256 = hashlib.sha256(video_bytes).hexdigest()

        try:
            response = await self._client._stub.UploadVideo(  # type: ignore[attr-defined]
                self._video_chunks_from_bytes(
                    ad_account_id=account_id,
                    filename=filename,
                    file_size=file_size,
                    content_sha256=content_sha256,
                    video_bytes=video_bytes,
                ),
                timeout=_UPLOAD_TIMEOUT_SECONDS,
            )
        except grpc.RpcError as exc:  # type: ignore[misc]
            await self._raise_grpc_error(exc, endpoint=endpoint)

        if not response.ok:
            err_msg = response.error or "UploadVideo: неизвестная ошибка"
            raise self._upload_response_error(err_msg, endpoint=endpoint)

        if not response.video_id:
            raise AmbiguousResultError(
                "UploadVideo вернул ok=true без video_id после dispatch",
                endpoint=endpoint,
            )

        self._client._remember_campaign_uploaded_video_id(
            response.video_id,
            ad_account_id=account_id,
        )
        return response.video_id

    async def wait_video_ready(
        self,
        video_id: str,
        *,
        ad_account_id: str,
        timeout: float = VIDEO_READY_TIMEOUT_SECONDS,  # noqa: ASYNC109 — deadline-поллинг с graceful False, не cancel
        interval: float = VIDEO_READY_POLL_INTERVAL_SECONDS,
    ) -> bool:
        """Дожидается status=ready видео ПЕРЕД созданием AdCreative.

        Meta выдаёт video_id на фазе finish загрузки, но транскодинг идёт асинхронно.
        Создание adcreative со свежим video_id, пока видео в processing, Meta часто
        отклоняет (1487056/1487202 'video is still being processed') и авто-thumbnail
        ещё недоступен → creative падает → PartialCreateError → orphan-залив на ручную
        чистку. Поллит GET /{video_id}?fields=status.

        Возвращает True если дождались ready; False если истёк timeout (best-effort —
        НЕ валим залив, даём Meta шанс принять creative). Ошибки чтения статуса
        проглатываются и поллинг продолжается (статус-GET не должен ронять залив).
        Бросает PermanentError только при явном status=error (видео не обработалось).
        """
        deadline = time.monotonic() + timeout
        last_status = ""
        while True:
            video_status = ""
            try:
                resp = await self._client.execute_graph_call(
                    method="GET",
                    endpoint=f"/{video_id}",
                    query_params={"fields": "status"},
                    ad_account_id=ad_account_id,
                )
                status_obj = (resp or {}).get("status") or {}
                video_status = str(status_obj.get("video_status", "")).lower()
                last_status = video_status or last_status
            except BrowserReadinessRejectedError:
                raise
            except Exception as exc:  # noqa: BLE001 — статус-GET best-effort, не валит залив
                logger.warning(
                    "video %s: ошибка чтения статуса (продолжаю поллинг): %s",
                    video_id,
                    safe_exception_diagnostic(exc),
                )

            if video_status == "ready":
                return True
            if video_status == "error":
                raise PermanentError(
                    f"видео {video_id} не обработалось Meta (status=error)",
                    endpoint=f"/{video_id}",
                )
            if time.monotonic() >= deadline:
                logger.warning(
                    "video %s не дошло до ready за %.0fs (последний статус=%s) — продолжаю залив",
                    video_id,
                    timeout,
                    last_status or "unknown",
                )
                return False
            await asyncio.sleep(interval)

    async def get_video_thumbnail_url(
        self,
        video_id: str,
        *,
        ad_account_id: str,
        retries: int = 6,
        interval: float = 3.0,
    ) -> str:
        """uri авто-сгенерённой Meta миниатюры видео (для video_data.image_url).

        Meta ТРЕБУЕТ image_hash ИЛИ image_url в video_data, иначе adcreatives падает
        subcode 1443226 «Для вашего объявления нужна миниатюра видео». Берём
        preferred-миниатюру из GET /{video_id}/thumbnails. Поллит — миниатюры
        появляются после обработки видео. Best-effort: пустая строка если не получили
        (creative тогда упадёт явно с понятной ошибкой).
        """
        last = ""
        for _ in range(retries):
            try:
                resp = await self._client.execute_graph_call(
                    method="GET",
                    endpoint=f"/{video_id}/thumbnails",
                    query_params={"fields": "uri,is_preferred"},
                    ad_account_id=ad_account_id,
                )
                data = (resp or {}).get("data") or []
                if data:
                    preferred = next((t for t in data if t.get("is_preferred")), None)
                    uri = str((preferred or data[0]).get("uri", ""))
                    if uri:
                        return uri
            except BrowserReadinessRejectedError:
                raise
            except Exception as exc:  # noqa: BLE001 — best-effort, поллим дальше
                last = safe_exception_diagnostic(exc)
                logger.warning("get_video_thumbnail_url %s failed (%s)", video_id, last)
            await asyncio.sleep(interval)
        logger.warning("видео %s: миниатюра не получена (%s)", video_id, last or "пусто")
        return ""

    # ====================== внутреннее ======================

    async def _video_chunks(
        self,
        *,
        ad_account_id: str,
        filename: str,
        file_size: int,
        content_sha256: str,
        video_path: Path,
    ) -> AsyncIterator[meta_api_pb2.UploadVideoChunk]:
        """Async iterator: читает файл и эмитит UploadVideoChunk-сообщения.

        Sync open() здесь намеренно: чтение чанка с диска быстрее, чем его
        отправка по сети. Использовать aiofiles ради не-блокирующего read()
        даст лишь маргинальное улучшение в стандартном случае.
        """
        authorization = await self._client.prepare_operation_authorization(
            rpc="upload_video",
            operation=media_operation_binding(
                rpc="upload_video",
                attributes={
                    "filename": filename,
                    "file_size": file_size,
                    "content_sha256": content_sha256,
                },
            ),
            ad_account_id=ad_account_id,
        )
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
                        ad_account_id=ad_account_id,
                        filename=filename,
                        file_size=file_size,
                        chunk_bytes=chunk_data,
                        chunk_index=chunk_index,
                        is_last_chunk=is_last,
                        is_init=False,
                        **authorization,
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
        content_sha256: str,
        video_bytes: bytes,
    ) -> AsyncIterator[meta_api_pb2.UploadVideoChunk]:
        """Async iterator из in-memory bytes."""
        authorization = await self._client.prepare_operation_authorization(
            rpc="upload_video",
            operation=media_operation_binding(
                rpc="upload_video",
                attributes={
                    "filename": filename,
                    "file_size": file_size,
                    "content_sha256": content_sha256,
                },
            ),
            ad_account_id=ad_account_id,
        )
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
                    ad_account_id=ad_account_id,
                    filename=filename,
                    file_size=file_size,
                    chunk_bytes=chunk_data,
                    chunk_index=chunk_index,
                    is_last_chunk=is_last,
                    is_init=False,
                    **authorization,
                )
            else:
                yield meta_api_pb2.UploadVideoChunk(
                    chunk_bytes=chunk_data,
                    chunk_index=chunk_index,
                    is_last_chunk=is_last,
                )
            chunk_index += 1
            bytes_sent = new_bytes_sent

    async def _raise_grpc_error(
        self,
        exc: grpc.RpcError,
        *,
        endpoint: str,
    ) -> NoReturn:
        readiness_error = await self._client._controlled_presend_readiness_error(  # noqa: SLF001
            exc,
            endpoint=endpoint,
        )
        if readiness_error is not None:
            raise readiness_error from exc
        # Capability/lease authorization and post-dispatch transport semantics
        # remain owned by the upload mapper below.
        raise self._grpc_to_error(exc, endpoint=endpoint) from exc

    @staticmethod
    def _grpc_to_error(exc: grpc.RpcError, *, endpoint: str) -> MetaApiError:
        """Преобразовать gRPC-ошибку upload-вызова в доменную."""
        code = exc.code() if hasattr(exc, "code") else None  # type: ignore[union-attr]
        code_name = code.name if code is not None and hasattr(code, "name") else "UNKNOWN"

        if code == grpc.StatusCode.FAILED_PRECONDITION:
            return SessionUnavailableError(
                f"Vision-сессия не готова к upload (gRPC {code_name})",
                endpoint=endpoint,
            )
        if code in (grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.PERMISSION_DENIED):
            return PermanentError(
                f"browser operation authorization rejected (gRPC {code_name})",
                endpoint=endpoint,
            )
        # After an upload RPC has been dispatched, a transport error is not
        # evidence that Meta did not accept the media.  The exact-session and
        # contract preconditions are converted by
        # _controlled_presend_readiness_error before reaching this mapper.
        return AmbiguousResultError(
            f"gRPC response lost after upload dispatch ({code_name})",
            endpoint=endpoint,
        )

    @staticmethod
    def _upload_response_error(message: str, *, endpoint: str) -> MetaApiError:
        """Classify only explicit server rejections as permanent.

        The v5 browser service reports exact-session/contract failures via gRPC
        status before browser I/O.  A normal ``ok=false`` technical response is
        therefore an invalid/legacy transport shape and cannot prove whether
        Meta accepted the upload.
        """
        if message.startswith(("GRAPH_ERROR_", "INVALID_ARGUMENT")):
            match = re.match(r"^(GRAPH_ERROR_-?[0-9]+|INVALID_ARGUMENT)\b", message)
            code = match.group(1) if match is not None else "external_rejection"
            return PermanentError(f"upload rejected ({code})", endpoint=endpoint)
        return AmbiguousResultError(
            "unstructured upload failure after dispatch",
            endpoint=endpoint,
        )
