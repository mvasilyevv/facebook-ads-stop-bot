# -*- coding: utf-8 -*-
"""Downloader media (видео/картинки) для Ad Library.

Скачивает media URLs из raw_json, сохраняет на диск, дедуп по sha256.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

DEFAULT_MEDIA_ROOT = Path("./data/ad_library_media")


@dataclass
class MediaItem:
    """Один media-файл для скачивания."""

    media_type: str  # video / image / thumbnail
    url: str


def extract_media_urls(raw_ad: dict[str, Any]) -> list[MediaItem]:
    """Достаёт media URLs из raw GraphQL ad-структуры.

    Видео: snapshot.videos[].video_hd_url / video_sd_url
    Картинки: snapshot.images[].original_image_url
    Thumbnail: snapshot.videos[].video_preview_image_url
    """
    items: list[MediaItem] = []
    snap = raw_ad.get("snapshot") or {}

    for video in snap.get("videos") or []:
        if not isinstance(video, dict):
            continue
        url = (
            video.get("video_hd_url")
            or video.get("video_sd_url")
            or video.get("watermarked_video_hd_url")
            or video.get("watermarked_video_sd_url")
        )
        if url:
            items.append(MediaItem(media_type="video", url=url))
        thumb = video.get("video_preview_image_url") or video.get("thumbnail_url")
        if thumb:
            items.append(MediaItem(media_type="thumbnail", url=thumb))

    for image in snap.get("images") or []:
        if not isinstance(image, dict):
            continue
        url = (
            image.get("original_image_url")
            or image.get("watermarked_resized_image_url")
            or image.get("resized_image_url")
        )
        if url:
            items.append(MediaItem(media_type="image", url=url))

    # Карусели — у каждой карточки могут быть свои медиа
    for card in snap.get("cards") or []:
        if not isinstance(card, dict):
            continue
        img = card.get("original_image_url") or card.get("resized_image_url")
        if img:
            items.append(MediaItem(media_type="image", url=img))
        vid = card.get("video_hd_url") or card.get("video_sd_url")
        if vid:
            items.append(MediaItem(media_type="video", url=vid))

    return items


def _file_extension(media_type: str, content_type: str | None) -> str:
    """Расширение по media_type + Content-Type заголовку."""
    if media_type == "video":
        return ".mp4"
    if content_type:
        ct = content_type.lower()
        if "jpeg" in ct or "jpg" in ct:
            return ".jpg"
        if "png" in ct:
            return ".png"
        if "webp" in ct:
            return ".webp"
        if "gif" in ct:
            return ".gif"
    return ".jpg"


async def download_one_media(
    engine: AsyncEngine,
    *,
    ad_archive_id: int,
    country: str,
    item: MediaItem,
    media_root: Path = DEFAULT_MEDIA_ROOT,
    timeout_s: float = 30.0,
) -> str | None:
    """Скачивает один media-файл, дедуп по sha256, INSERT в БД.

    Returns: local_path если скачано/уже есть, иначе None.
    """
    media_root = Path(media_root).resolve()  # noqa: ASYNC240
    out_dir = media_root / country / str(ad_archive_id)
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as http:
            response = await http.get(item.url)
            response.raise_for_status()
            content = response.content
            content_type = response.headers.get("content-type")
    except Exception as exc:
        logger.warning("Download failed: %s (%s)", item.url, exc)
        return None

    sha = hashlib.sha256(content).hexdigest()

    # Проверка дедупа: если уже есть запись с этим sha256 — не скачиваем заново
    async with engine.connect() as conn:
        existing = (
            await conn.execute(
                text("SELECT local_path FROM ad_library_media WHERE sha256 = :sha"),
                {"sha": sha},
            )
        ).first()
    if existing:
        return existing[0]

    ext = _file_extension(item.media_type, content_type)
    filename = f"{item.media_type}_{sha[:12]}{ext}"
    out_path = out_dir / filename
    out_path.write_bytes(content)
    file_size = len(content)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO ad_library_media
                    (ad_archive_id, media_type, local_path, sha256, file_size_bytes)
                VALUES (:aid, :mt, :lp, :sha, :fs)
                ON CONFLICT (sha256) DO NOTHING
                """
            ),
            {
                "aid": ad_archive_id,
                "mt": item.media_type,
                "lp": str(out_path),
                "sha": sha,
                "fs": file_size,
            },
        )
    logger.info("Downloaded %s → %s (%d bytes)", item.url[:60], out_path.name, file_size)
    return str(out_path)


async def download_for_scan(
    engine: AsyncEngine,
    *,
    scan_id: str,
    country: str,
    media_root: Path = DEFAULT_MEDIA_ROOT,
    max_per_ad: int = 3,
) -> dict[str, int]:
    """Скачивает медиа для всех ads в данном scan'е.

    max_per_ad: ограничение количества файлов на одно объявление (защита от тонн thumbnail'ов).
    Returns: {downloaded, skipped, failed}.
    """
    counts = {"downloaded": 0, "skipped": 0, "failed": 0}

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT ad_archive_id, raw_json
                FROM ad_library_snapshot
                WHERE scan_id = :sid
                """
            ),
            {"sid": scan_id},
        )
        rows = [(r[0], r[1]) for r in result]

    for ad_archive_id, raw_json in rows:
        items = extract_media_urls(raw_json or {})[:max_per_ad]
        for item in items:
            try:
                local_path = await download_one_media(
                    engine,
                    ad_archive_id=ad_archive_id,
                    country=country,
                    item=item,
                    media_root=media_root,
                )
                if local_path:
                    counts["downloaded"] += 1
                else:
                    counts["failed"] += 1
            except Exception as exc:
                logger.warning("Download error for ad %d: %s", ad_archive_id, exc)
                counts["failed"] += 1

    logger.info("Media downloaded for scan %s: %s", scan_id, counts)
    return counts
