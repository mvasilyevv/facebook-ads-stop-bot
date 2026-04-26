# -*- coding: utf-8 -*-
"""Тесты уникализации и раскладки креативов."""

from __future__ import annotations

import io
from datetime import datetime

import pytest
from PIL import Image

from core.creatives.service import (
    CreativeInput,
    CreativeValidationError,
    build_iteration_name,
    uniquify_creatives,
)


def _make_png_bytes(width: int = 96, height: int = 72) -> bytes:
    """Создаёт тестовое изображение с градиентом."""
    image = Image.new("RGB", (width, height))
    for x in range(width):
        for y in range(height):
            image.putpixel((x, y), ((x * 3) % 255, (y * 4) % 255, (x + y) % 255))

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


# Сценарий: сервис создаёт папку запуска, папки копий и JPEG без EXIF.
@pytest.mark.asyncio
async def test_uniquify_creatives_creates_copy_directories_and_jpegs(tmp_path):
    now = datetime(2026, 4, 26, 14, 35, 9)
    source = CreativeInput(filename="DRC_CR2_CR001.png", content=_make_png_bytes())

    result = await uniquify_creatives(
        offer_name="DRC_CR2",
        copies=2,
        creatives=[source],
        base_dir=tmp_path,
        now=now,
    )

    assert result.iteration_name == "DRC_CR2_2026-04-26_14-35-09_1creo_2copies"
    assert result.creative_count == 1
    assert result.copy_count == 2
    assert len(result.files) == 2

    first = tmp_path / result.iteration_name / "1" / "DRC_CR2_CR001_1.jpeg"
    second = tmp_path / result.iteration_name / "2" / "DRC_CR2_CR001_2.jpeg"
    assert first.exists()
    assert second.exists()
    assert first.read_bytes() != second.read_bytes()

    with Image.open(first) as image:
        assert image.format == "JPEG"
        assert image.size == (96, 72)
        assert len(image.getexif()) == 0
        assert "exif" not in image.info


# Сценарий: повторный запуск с тем же именем полностью заменяет старую папку.
@pytest.mark.asyncio
async def test_uniquify_creatives_replaces_existing_iteration_directory(tmp_path):
    now = datetime(2026, 4, 26, 14, 35, 9)
    iteration_name = build_iteration_name(
        offer_name="DRC_CR2",
        now=now,
        creative_count=1,
        copy_count=1,
    )
    old_dir = tmp_path / iteration_name / "1"
    old_dir.mkdir(parents=True)
    old_file = old_dir / "old.jpeg"
    old_file.write_bytes(b"old")

    await uniquify_creatives(
        offer_name="DRC_CR2",
        copies=1,
        creatives=[CreativeInput(filename="new.png", content=_make_png_bytes())],
        base_dir=tmp_path,
        now=now,
    )

    assert not old_file.exists()
    assert (tmp_path / iteration_name / "1" / "new_1.jpeg").exists()


# Сценарий: пустой список креативов отклоняется до создания папок.
@pytest.mark.asyncio
async def test_uniquify_creatives_rejects_empty_creative_list(tmp_path):
    with pytest.raises(CreativeValidationError, match="Загрузите хотя бы один креатив"):
        await uniquify_creatives(
            offer_name="DRC_CR2",
            copies=1,
            creatives=[],
            base_dir=tmp_path,
            now=datetime(2026, 4, 26, 14, 35, 9),
        )

    assert list(tmp_path.iterdir()) == []
