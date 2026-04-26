# -*- coding: utf-8 -*-
"""Оркестрация уникализации креативов и записи результата на диск."""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from core.creatives.uniquifier import CreativeImageError, uniquify_image_bytes

MAX_CREATIVE_COUNT = 20
MAX_COPY_COUNT = 50
MAX_CREATIVE_BYTES = 30 * 1024 * 1024


class CreativeValidationError(ValueError):
    """Ошибка пользовательских параметров уникализации."""


@dataclass(frozen=True)
class CreativeInput:
    """Исходный файл креатива."""

    filename: str
    content: bytes


@dataclass(frozen=True)
class CreativeOutputFile:
    """Один сохранённый JPEG-файл."""

    copy_index: int
    source_name: str
    output_name: str
    output_path: str


@dataclass(frozen=True)
class CreativeUniquifyResult:
    """Результат пакетной уникализации."""

    root_dir: str
    iteration_dir: str
    iteration_name: str
    creative_count: int
    copy_count: int
    files: list[CreativeOutputFile]


def default_creatives_root() -> Path:
    """Возвращает корневую папку для готовых креативов."""
    return Path.home() / "Documents" / "FB_Agent_Creo"


def _sanitize_path_part(value: str, *, fallback: str, max_len: int = 80) -> str:
    """Оставляет в имени только безопасные для Finder символы."""
    cleaned = re.sub(r"[^\wа-яА-ЯёЁ ._-]+", "_", value, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._- ")
    return (cleaned or fallback)[:max_len]


def _safe_source_stem(filename: str, index: int) -> str:
    """Возвращает безопасный stem исходного файла."""
    stem = Path(filename or "").name
    stem = Path(stem).stem
    return _sanitize_path_part(stem, fallback=f"creative_{index}")


def build_iteration_name(
    *, offer_name: str, now: datetime, creative_count: int, copy_count: int
) -> str:
    """Собирает имя папки запуска из оффера, времени и объёма пачки."""
    offer_slug = _sanitize_path_part(offer_name, fallback="offer", max_len=60)
    timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{offer_slug}_{timestamp}_{creative_count}creo_{copy_count}copies"


def _validate_inputs(offer_name: str, copies: int, creatives: list[CreativeInput]) -> None:
    """Проверяет параметры до записи файлов."""
    if not offer_name.strip():
        raise CreativeValidationError("Укажите название оффера")
    if copies < 1:
        raise CreativeValidationError("Количество копий должно быть не меньше 1")
    if copies > MAX_COPY_COUNT:
        raise CreativeValidationError(f"Количество копий не должно превышать {MAX_COPY_COUNT}")
    if not creatives:
        raise CreativeValidationError("Загрузите хотя бы один креатив")
    if len(creatives) > MAX_CREATIVE_COUNT:
        raise CreativeValidationError(
            f"За один запуск можно обработать не больше {MAX_CREATIVE_COUNT} креативов"
        )

    for index, creative in enumerate(creatives, start=1):
        if not creative.content:
            raise CreativeValidationError(f"Файл «{creative.filename or index}» пустой")
        if len(creative.content) > MAX_CREATIVE_BYTES:
            raise CreativeValidationError(
                f"Файл «{creative.filename or index}» больше лимита 30 МБ"
            )


async def _remove_directory(path: Path) -> None:
    """Удаляет директорию в отдельном потоке."""
    exists = await asyncio.to_thread(path.exists)
    if exists:
        await asyncio.to_thread(shutil.rmtree, path)


async def _write_file(path: Path, content: bytes) -> None:
    """Записывает файл в отдельном потоке."""
    await asyncio.to_thread(path.write_bytes, content)


async def uniquify_creatives(
    *,
    offer_name: str,
    copies: int,
    creatives: list[CreativeInput],
    base_dir: Path | None = None,
    now: datetime | None = None,
) -> CreativeUniquifyResult:
    """Создаёт папки копий и сохраняет уникализированные JPEG."""
    _validate_inputs(offer_name, copies, creatives)

    root_dir = base_dir or default_creatives_root()
    run_at = now or datetime.now()
    iteration_name = build_iteration_name(
        offer_name=offer_name,
        now=run_at,
        creative_count=len(creatives),
        copy_count=copies,
    )
    iteration_dir = root_dir / iteration_name
    temp_dir = root_dir / f".tmp_{iteration_name}_{uuid4().hex}"
    saved_files: list[CreativeOutputFile] = []

    await asyncio.to_thread(root_dir.mkdir, parents=True, exist_ok=True)
    await _remove_directory(temp_dir)

    try:
        await asyncio.to_thread(temp_dir.mkdir, parents=True, exist_ok=False)
        for copy_index in range(1, copies + 1):
            copy_dir = temp_dir / str(copy_index)
            await asyncio.to_thread(copy_dir.mkdir, parents=True, exist_ok=False)

            for creative_index, creative in enumerate(creatives, start=1):
                output_name = (
                    f"{_safe_source_stem(creative.filename, creative_index)}_{copy_index}.jpeg"
                )
                output_path = copy_dir / output_name
                try:
                    jpeg_bytes = await asyncio.to_thread(
                        uniquify_image_bytes,
                        creative.content,
                        source_name=creative.filename or f"creative_{creative_index}",
                        copy_index=copy_index,
                        creative_index=creative_index,
                        run_slug=iteration_name,
                    )
                except CreativeImageError as exc:
                    raise CreativeValidationError(str(exc)) from exc

                await _write_file(output_path, jpeg_bytes)
                saved_files.append(
                    CreativeOutputFile(
                        copy_index=copy_index,
                        source_name=creative.filename,
                        output_name=output_name,
                        output_path=str(output_path),
                    )
                )

        await _remove_directory(iteration_dir)
        await asyncio.to_thread(temp_dir.rename, iteration_dir)
    except Exception:
        await _remove_directory(temp_dir)
        raise

    final_files = [
        CreativeOutputFile(
            copy_index=file.copy_index,
            source_name=file.source_name,
            output_name=file.output_name,
            output_path=str(iteration_dir / str(file.copy_index) / file.output_name),
        )
        for file in saved_files
    ]

    return CreativeUniquifyResult(
        root_dir=str(root_dir),
        iteration_dir=str(iteration_dir),
        iteration_name=iteration_name,
        creative_count=len(creatives),
        copy_count=copies,
        files=final_files,
    )
