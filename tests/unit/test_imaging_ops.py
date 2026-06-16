# -*- coding: utf-8 -*-
"""Unit: детерминированные Pillow-операции core.imaging.ops (без сети/AI)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from core.imaging import ops


def _img(w: int, h: int, color: tuple[int, int, int, int]) -> Image.Image:
    return Image.new("RGBA", (w, h), color)


# resize_exact даёт ровно заданный размер.
def test_resize_exact() -> None:
    out = ops.resize_exact(_img(100, 100, (255, 0, 0, 255)), 50, 25)
    assert out.size == (50, 25)


# crop_to_aspect: центр-кроп под соотношение → точный итоговый размер.
def test_crop_to_aspect_size() -> None:
    out = ops.crop_to_aspect(_img(100, 100, (0, 0, 0, 255)), 50, 25)
    assert out.size == (50, 25)


# crop_to_aspect широкого источника обрезает по ширине (соотношение соблюдено).
def test_crop_to_aspect_wide_source() -> None:
    out = ops.crop_to_aspect(_img(400, 100, (0, 0, 0, 255)), 100, 100)
    assert out.size == (100, 100)


# overlay_text не меняет размер, но меняет пиксели (текст реально нарисован).
def test_overlay_text_changes_pixels() -> None:
    base = _img(120, 50, (255, 255, 255, 255))
    out = ops.overlay_text(base, "HI", xy=(10, 10), size=24, color="#000000")
    assert out.size == base.size
    assert out.tobytes() != base.tobytes()


# composite кладёт непрозрачный слой в позицию — пиксель под ним меняется на цвет слоя.
def test_composite_overlays_layer() -> None:
    bg = _img(50, 50, (255, 0, 0, 255))  # красный
    fg = _img(10, 10, (0, 0, 255, 255))  # синий, непрозрачный
    out = ops.composite(bg, fg, xy=(0, 0))
    assert out.getpixel((5, 5))[:3] == (0, 0, 255)
    assert out.getpixel((40, 40))[:3] == (255, 0, 0)


# cover_region заливает зону цветом (удаление вотермарки) — вне зоны нетронуто.
def test_cover_region_fill() -> None:
    base = _img(20, 20, (255, 255, 255, 255))
    out = ops.cover_region(base, (5, 5, 15, 15), color="#000000")
    assert out.getpixel((10, 10))[:3] == (0, 0, 0)
    assert out.getpixel((0, 0))[:3] == (255, 255, 255)


# adjust с дефолтами (всё 1.0) не падает и сохраняет размер.
def test_adjust_identity() -> None:
    base = _img(30, 30, (100, 120, 140, 255))
    out = ops.adjust(base)
    assert out.size == base.size


# save в .jpg схлопывает альфу (jpeg без прозрачности) и пишет файл.
def test_save_jpg(tmp_path: Path) -> None:
    out = ops.save(_img(10, 10, (10, 20, 30, 128)), tmp_path / "x.jpg")
    assert out.exists()
    reopened = Image.open(out)
    assert reopened.mode == "RGB"


# to_format конвертирует расширение и создаёт новый файл.
def test_to_format(tmp_path: Path) -> None:
    src = tmp_path / "a.png"
    ops.save(_img(8, 8, (1, 2, 3, 255)), src)
    out = ops.to_format(src, "jpg")
    assert out.suffix == ".jpg" and out.exists()
