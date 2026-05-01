# -*- coding: utf-8 -*-
"""Асинхронное чтение и валидация папок креативов для сценария кампаний."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from core.creatives.service import default_creatives_root

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


class CampaignCreativeValidationError(ValueError):
    """Ошибка структуры папки креативов для создания кампании."""


@dataclass(frozen=True)
class CampaignCreativeFile:
    """Один медиафайл объявления внутри папки копии."""

    adset_index: int
    ad_name: str
    media_file_name: str
    media_search_name: str
    media_path: str
    media_type: str


@dataclass(frozen=True)
class CampaignCreativeAdSet:
    """Набор креативов для одной группы объявлений."""

    index: int
    name: str
    folder_path: str
    files: list[CampaignCreativeFile]


@dataclass(frozen=True)
class CampaignCreativeFolder:
    """Проверенная папка креативов для одного запуска."""

    name: str
    path: str
    media_type: str
    adsets: list[CampaignCreativeAdSet]


@dataclass(frozen=True)
class CampaignCreativeFolderSummary:
    """Краткое описание папки креативов для выбора в UI."""

    name: str
    path: str
    adset_count: int
    creative_count: int
    media_type: str
    updated_at: float
    is_valid: bool = True
    validation_error: str = ""


def _media_type_for_extension(extension: str) -> str:
    """Возвращает тип медиа по расширению."""
    normalized = extension.lower()
    if normalized in IMAGE_EXTENSIONS:
        return "image"
    if normalized in VIDEO_EXTENSIONS:
        return "video"
    raise CampaignCreativeValidationError(f"Неподдерживаемый тип файла: {extension}")


def _resolve_folder(folder_name: str, root: Path | None = None) -> Path:
    """Возвращает безопасный путь к папке внутри корня креативов."""
    root_path = (root or default_creatives_root()).expanduser().resolve()
    raw_path = Path(folder_name).expanduser()
    candidate = raw_path if raw_path.is_absolute() else root_path / raw_path
    resolved = candidate.resolve()

    if not resolved.is_relative_to(root_path):
        raise CampaignCreativeValidationError("Папка креативов должна быть внутри FB_Agent_Creo")
    if not resolved.exists() or not resolved.is_dir():
        raise CampaignCreativeValidationError(f"Папка креативов не найдена: {resolved.name}")
    return resolved


def _read_media_files(folder: Path) -> list[Path]:
    """Читает поддерживаемые медиафайлы из папки."""
    files = [
        path
        for path in folder.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(files, key=lambda item: item.name.casefold())


def _parse_ad_name(file_path: Path, adset_index: int) -> str:
    """Удаляет суффикс копии из имени файла и получает имя объявления."""
    suffix = f"_{adset_index}"
    stem = file_path.stem
    if not stem.endswith(suffix):
        raise CampaignCreativeValidationError(
            f"Файл {file_path.name} должен заканчиваться на {suffix}"
        )
    ad_name = stem[: -len(suffix)].strip()
    if not ad_name:
        raise CampaignCreativeValidationError(
            f"Не удалось получить имя объявления из {file_path.name}"
        )
    return ad_name


def _inspect_creative_folder_sync(
    folder_name: str, root: Path | None = None
) -> CampaignCreativeFolder:
    """Синхронно проверяет структуру папки креативов."""
    folder = _resolve_folder(folder_name, root)
    adset_dirs = sorted(
        [path for path in folder.iterdir() if path.is_dir() and path.name.isdigit()],
        key=lambda item: int(item.name),
    )
    if not adset_dirs:
        raise CampaignCreativeValidationError("В папке нет подпапок ad set с номерами 1, 2, 3...")

    expected_indexes = list(range(1, len(adset_dirs) + 1))
    actual_indexes = [int(path.name) for path in adset_dirs]
    if actual_indexes != expected_indexes:
        expected = ", ".join(str(value) for value in expected_indexes)
        actual = ", ".join(str(value) for value in actual_indexes)
        raise CampaignCreativeValidationError(
            f"Подпапки ad set должны идти подряд: ожидали {expected}, нашли {actual}"
        )

    expected_ad_names: list[str] | None = None
    folder_media_type: str | None = None
    adsets: list[CampaignCreativeAdSet] = []

    for adset_dir in adset_dirs:
        adset_index = int(adset_dir.name)
        media_files = _read_media_files(adset_dir)
        if not media_files:
            raise CampaignCreativeValidationError(f"В подпапке {adset_dir.name} нет медиафайлов")

        creatives: list[CampaignCreativeFile] = []
        seen_ad_names: set[str] = set()
        adset_media_types: set[str] = set()

        for media_file in media_files:
            media_type = _media_type_for_extension(media_file.suffix)
            adset_media_types.add(media_type)
            ad_name = _parse_ad_name(media_file, adset_index)
            if ad_name in seen_ad_names:
                raise CampaignCreativeValidationError(
                    f"В подпапке {adset_dir.name} повторяется объявление {ad_name}"
                )
            seen_ad_names.add(ad_name)
            creatives.append(
                CampaignCreativeFile(
                    adset_index=adset_index,
                    ad_name=ad_name,
                    media_file_name=media_file.name,
                    media_search_name=media_file.stem,
                    media_path=str(media_file),
                    media_type=media_type,
                )
            )

        if len(adset_media_types) != 1:
            raise CampaignCreativeValidationError(
                f"В подпапке {adset_dir.name} смешаны фото и видео. Для одного запуска нужен один тип медиа"
            )

        adset_media_type = next(iter(adset_media_types))
        if folder_media_type is None:
            folder_media_type = adset_media_type
        elif folder_media_type != adset_media_type:
            raise CampaignCreativeValidationError(
                "В разных подпапках найден разный тип медиа. Для одного запуска нужен один тип медиа"
            )

        ad_names = sorted(seen_ad_names)
        if expected_ad_names is None:
            expected_ad_names = ad_names
        elif ad_names != expected_ad_names:
            raise CampaignCreativeValidationError(
                f"Набор объявлений в подпапке {adset_dir.name} отличается от подпапки 1"
            )

        adsets.append(
            CampaignCreativeAdSet(
                index=adset_index,
                name=str(adset_index),
                folder_path=str(adset_dir),
                files=sorted(creatives, key=lambda item: item.ad_name.casefold()),
            )
        )

    return CampaignCreativeFolder(
        name=folder.name,
        path=str(folder),
        media_type=folder_media_type or "image",
        adsets=adsets,
    )


def _summarize_folder_sync(folder: Path, root: Path) -> CampaignCreativeFolderSummary | None:
    """Собирает краткую информацию о папке, пропуская неподходящие варианты."""
    try:
        inspected = _inspect_creative_folder_sync(folder.name, root)
    except CampaignCreativeValidationError as exc:
        stat = folder.stat()
        return CampaignCreativeFolderSummary(
            name=folder.name,
            path=str(folder),
            adset_count=0,
            creative_count=0,
            media_type="unknown",
            updated_at=stat.st_mtime,
            is_valid=False,
            validation_error=str(exc),
        )

    stat = folder.stat()
    return CampaignCreativeFolderSummary(
        name=inspected.name,
        path=inspected.path,
        adset_count=len(inspected.adsets),
        creative_count=len(inspected.adsets[0].files) if inspected.adsets else 0,
        media_type=inspected.media_type,
        updated_at=stat.st_mtime,
        is_valid=True,
        validation_error="",
    )


def _list_creative_folders_sync(
    root: Path | None = None, limit: int = 100
) -> list[CampaignCreativeFolderSummary]:
    """Синхронно читает список валидных папок креативов."""
    root_path = (root or default_creatives_root()).expanduser().resolve()
    if not root_path.exists():
        return []

    folders = [
        path for path in root_path.iterdir() if path.is_dir() and not path.name.startswith(".")
    ]
    folders.sort(key=lambda item: item.stat().st_mtime, reverse=True)

    summaries: list[CampaignCreativeFolderSummary] = []
    for folder in folders:
        summary = _summarize_folder_sync(folder, root_path)
        if summary is not None:
            summaries.append(summary)
        if len(summaries) >= limit:
            break
    return summaries


async def inspect_creative_folder(
    folder_name: str,
    root: Path | None = None,
) -> CampaignCreativeFolder:
    """Асинхронно проверяет выбранную папку креативов."""
    return await asyncio.to_thread(_inspect_creative_folder_sync, folder_name, root)


async def list_creative_folders(
    root: Path | None = None,
    limit: int = 100,
) -> list[CampaignCreativeFolderSummary]:
    """Асинхронно возвращает список валидных папок креативов."""
    return await asyncio.to_thread(_list_creative_folders_sync, root, limit)
