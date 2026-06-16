# -*- coding: utf-8 -*-
"""Frozen dataclasses-контракты для syntx.ai клиента.

- Scope          — раздел нейросетей (image/video/audio/text/tools).
- ModelInfo      — одна модель из /ai/models (model_type + лимиты файлов + cost-params).
- UploadedRef    — результат загрузки реф-картинки (/chats/upload-files).
- Balance        — баланс токенов (/user/balance).
- GenRequest     — высокоуровневый запрос на генерацию (image сейчас, video — заложен).
- GenResult      — результат генерации (url'ы + локальные файлы + расход токенов).

Все dataclass frozen — DTO, не мутируются (как в core/adset_pro/schemas.py).
Контракт API снят вживую 16.06, см. reference-syntx-api-direct (память).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Разделы syntx. Совпадают со scope из GET /ai.
SCOPE_IMAGE = "image"
SCOPE_VIDEO = "video"
SCOPE_AUDIO = "audio"
SCOPE_TEXT = "text"
_KNOWN_SCOPES = frozenset({SCOPE_IMAGE, SCOPE_VIDEO, SCOPE_AUDIO, SCOPE_TEXT})


@dataclass(slots=True, frozen=True)
class ModelInfo:
    """Одна модель из каталога syntx.

    `ai_name` — значение для query `?ai_name=` в /design/generate (напр. sora-images,
    kling). `model_type` — конкретная модель внутри (settings.model_type, напр.
    gpt-image-2, kling_image2video). `scope` проставляется каталогом из /ai (в
    /ai/models его нет). `file_count_limit` бывает int либо dict по типу медиа
    (напр. {"images":7,"videos":1}) — храним как есть.
    """

    ai_name: str
    model_type: str
    label: str = ""
    default: bool = False
    scope: str | None = None
    allowed_media_types: tuple[str, ...] = ()
    accepted_file_types: tuple[str, ...] = ()
    file_count_limit: Any = 0
    get_cost_params: tuple[str, ...] = ()

    @classmethod
    def from_models_row(cls, row: dict[str, Any]) -> ModelInfo:
        """Распарсить строку из GET /ai/models (scope добавит каталог)."""
        settings = row.get("settings") or {}
        media = settings.get("allowed_media_types") or []
        accepted = settings.get("accepted_file_types") or []
        cost_params = settings.get("get_cost_params") or []
        return cls(
            ai_name=str(row.get("ai_name") or ""),
            model_type=str(row.get("value") or ""),
            label=str(row.get("label") or ""),
            default=bool(row.get("default")),
            allowed_media_types=tuple(media) if isinstance(media, list) else (),
            accepted_file_types=tuple(accepted) if isinstance(accepted, list) else (),
            file_count_limit=settings.get("file_count_limit", 0),
            get_cost_params=tuple(cost_params) if isinstance(cost_params, list) else (),
        )

    def max_files_for(self, media: str = "images") -> int:
        """Лимит файлов данного типа (file_count_limit бывает int либо dict)."""
        limit = self.file_count_limit
        if isinstance(limit, dict):
            try:
                return int(limit.get(media, 0))
            except (TypeError, ValueError):
                return 0
        try:
            return int(limit)
        except (TypeError, ValueError):
            return 0


@dataclass(slots=True, frozen=True)
class UploadedRef:
    """Загруженная реф-картинка (одна запись из files[] ответа upload-files)."""

    filename: str
    url: str
    status: str = ""
    content_type: str = ""
    size: int = 0
    hash: str = ""

    @classmethod
    def from_api_row(cls, row: dict[str, Any]) -> UploadedRef:
        return cls(
            filename=str(row.get("filename") or ""),
            url=str(row.get("url") or ""),
            status=str(row.get("status") or ""),
            content_type=str(row.get("content_type") or ""),
            size=int(row.get("size") or 0),
            hash=str(row.get("hash") or ""),
        )


@dataclass(slots=True, frozen=True)
class Balance:
    """Баланс токенов аккаунта (/user/balance). В syntx токены не сгорают."""

    tokens: float
    user_id: str = ""

    @classmethod
    def from_api_payload(cls, payload: dict[str, Any]) -> Balance:
        raw = payload.get("balance", 0)
        try:
            tokens = float(raw)
        except (TypeError, ValueError):
            tokens = 0.0
        return cls(tokens=tokens, user_id=str(payload.get("user_id") or ""))


@dataclass(slots=True, frozen=True)
class GenRequest:
    """Высокоуровневый запрос на генерацию.

    Поля image-генерации заполнены сейчас; video-поля (`video_duration`, `gen_type`)
    заложены под будущую интеграцию (см. SyntxClient.generate_video). `image_refs` —
    локальные пути ИЛИ готовые r2-url; клиент сам грузит локальные через upload-files.
    `extra` пробрасывается в settings как есть (для редких параметров модели).
    """

    scope: str
    ai_name: str
    model_type: str
    prompt: str
    image_refs: tuple[str, ...] = ()
    # маска для inpaint (локальный путь/url); None = правка без маски (instruction-edit)
    mask_ref: str | None = None
    # aspect_ratio=None → не навязывать (edit сохраняет исходные пропорции)
    aspect_ratio: str | None = "16:9"
    quality: str | None = "1K"  # только sora-images
    details_quality: str | None = "medium"  # только sora-images
    image_size: str | None = None  # только banana (напр. "2K")
    n: int = 1
    # --- video (заложено на будущее, сейчас не используется image-путём) ---
    video_duration: int | None = None
    gen_type: str | None = None  # settings.type для видео: references|first_frame|...
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.prompt or not self.prompt.strip():
            raise ValueError("GenRequest.prompt пустой")
        if self.scope not in _KNOWN_SCOPES:
            raise ValueError(f"GenRequest.scope неизвестен: {self.scope!r}")
        if self.n < 1:
            raise ValueError(f"GenRequest.n должен быть >= 1, получено {self.n}")
        if not self.ai_name or not self.model_type:
            raise ValueError("GenRequest требует ai_name и model_type")


@dataclass(slots=True, frozen=True)
class GenResult:
    """Результат генерации: url'ы результата + (опц.) скачанные файлы + расход."""

    chat_uuid: str
    ai_name: str
    model_type: str
    image_urls: tuple[str, ...] = ()
    local_paths: tuple[Path, ...] = ()
    message_id: int | None = None
    tokens_spent: float | None = None
