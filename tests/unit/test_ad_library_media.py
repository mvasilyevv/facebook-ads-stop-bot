# -*- coding: utf-8 -*-
"""Unit-тесты media.extract_media_urls (без сетевых запросов)."""

from __future__ import annotations

from core.ad_library.media import extract_media_urls


# Видео + превью извлекаются
def test_extract_video_with_preview() -> None:
    raw = {
        "snapshot": {
            "videos": [
                {
                    "video_hd_url": "https://example.com/hd.mp4",
                    "video_preview_image_url": "https://example.com/preview.jpg",
                }
            ]
        }
    }
    items = extract_media_urls(raw)
    types = [item.media_type for item in items]
    assert "video" in types
    assert "thumbnail" in types


# Изображения извлекаются
def test_extract_image() -> None:
    raw = {
        "snapshot": {
            "images": [
                {"original_image_url": "https://example.com/img.jpg"},
            ]
        }
    }
    items = extract_media_urls(raw)
    assert len(items) == 1
    assert items[0].media_type == "image"


# Карусель — несколько карт, каждая с media
def test_extract_carousel() -> None:
    raw = {
        "snapshot": {
            "cards": [
                {"original_image_url": "https://example.com/card1.jpg"},
                {"original_image_url": "https://example.com/card2.jpg"},
                {"video_sd_url": "https://example.com/card3.mp4"},
            ]
        }
    }
    items = extract_media_urls(raw)
    assert len([i for i in items if i.media_type == "image"]) == 2
    assert len([i for i in items if i.media_type == "video"]) == 1


# Если snapshot нет — пустой список
def test_extract_no_snapshot() -> None:
    assert extract_media_urls({}) == []


# Watermarked URL используется если основного нет
def test_extract_watermarked_fallback() -> None:
    raw = {"snapshot": {"videos": [{"watermarked_video_hd_url": "https://example.com/wm.mp4"}]}}
    items = extract_media_urls(raw)
    assert len(items) == 1
    assert items[0].url == "https://example.com/wm.mp4"
