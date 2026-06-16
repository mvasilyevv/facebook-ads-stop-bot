# -*- coding: utf-8 -*-
"""Детерминированные операции редактирования через Pillow.

Все функции принимают/возвращают PIL.Image.Image (кроме load/save) — их можно
сцеплять. Координаты/размеры в пикселях. Никакого AI: результат предсказуем.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

# Системные шрифты macOS (жирные, под лого/хедлайны), от приоритетных к запасным.
_FONT_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
)


def load(path: str | Path) -> Image.Image:
    """Загрузить изображение (в RGBA для безопасного композитинга/прозрачности)."""
    return Image.open(Path(path).expanduser()).convert("RGBA")


def save(img: Image.Image, path: str | Path, *, quality: int = 95) -> Path:
    """Сохранить. JPG/JPEG → схлопываем альфу на белый фон (jpeg не умеет alpha)."""
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        bg.save(out, quality=quality)
    else:
        img.save(out)
    return out


def resize_exact(img: Image.Image, width: int, height: int) -> Image.Image:
    """Точный ресайз в width×height (LANCZOS, без сохранения пропорций)."""
    return img.resize((width, height), Image.Resampling.LANCZOS)


def crop_to_aspect(img: Image.Image, width: int, height: int) -> Image.Image:
    """Центр-кроп под соотношение width:height, затем ресайз ровно в width×height.

    Повторяет ручной кроп feature-баннера (1280×720 → 1024×500): сначала режем
    по соотношению из центра, потом масштабируем в точный размер.
    """
    target = width / height
    cw, ch = img.size
    if cw / ch > target:
        crop_w, crop_h = int(ch * target), ch
    else:
        crop_w, crop_h = cw, int(cw / target)
    left = (cw - crop_w) // 2
    top = (ch - crop_h) // 2
    cropped = img.crop((left, top, left + crop_w, top + crop_h))
    return resize_exact(cropped, width, height)


def _load_font(font_path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [font_path, *(_FONT_CANDIDATES)] if font_path else list(_FONT_CANDIDATES)
    for cand in candidates:
        if cand and Path(cand).exists():
            try:
                return ImageFont.truetype(cand, size)
            except OSError:
                continue
    return ImageFont.load_default()


def overlay_text(
    img: Image.Image,
    text: str,
    *,
    xy: tuple[int, int],
    size: int = 48,
    color: str = "#FFFFFF",
    font_path: str | None = None,
    anchor: str = "la",
    stroke_width: int = 0,
    stroke_fill: str = "#000000",
) -> Image.Image:
    """Наложить текст в точную позицию точным шрифтом/цветом (чисто, без AI).

    anchor — как у Pillow (`la` верх-лево, `mm` центр, `ms` центр-низ baseline).
    stroke_* — обводка для читабельности на пёстром фоне.
    """
    out = img.copy()
    draw = ImageDraw.Draw(out)
    font = _load_font(font_path, size)
    draw.text(
        xy,
        text,
        fill=color,
        font=font,
        anchor=anchor,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )
    return out


def composite(
    bg: Image.Image,
    fg: Image.Image,
    *,
    xy: tuple[int, int] = (0, 0),
    scale: float = 1.0,
) -> Image.Image:
    """Наложить fg на bg в позицию xy с учётом альфы (слой). scale — масштаб fg."""
    base = bg.convert("RGBA").copy()
    layer = fg.convert("RGBA")
    if scale != 1.0:
        layer = resize_exact(
            layer, max(1, int(layer.width * scale)), max(1, int(layer.height * scale))
        )
    base.alpha_composite(layer, dest=xy)
    return base


def adjust(
    img: Image.Image,
    *,
    brightness: float = 1.0,
    contrast: float = 1.0,
    color: float = 1.0,
    sharpness: float = 1.0,
) -> Image.Image:
    """Цветокор: яркость/контраст/насыщенность/резкость (1.0 = без изменений)."""
    out = img
    if brightness != 1.0:
        out = ImageEnhance.Brightness(out).enhance(brightness)
    if contrast != 1.0:
        out = ImageEnhance.Contrast(out).enhance(contrast)
    if color != 1.0:
        out = ImageEnhance.Color(out).enhance(color)
    if sharpness != 1.0:
        out = ImageEnhance.Sharpness(out).enhance(sharpness)
    return out


def cover_region(
    img: Image.Image,
    box: tuple[int, int, int, int],
    *,
    color: str | None = None,
    blur: bool = False,
) -> Image.Image:
    """Закрыть зону box=(l,t,r,b): залить цветом ИЛИ заблюрить (удаление вотермарки).

    color задан → заливка; blur=True → блюр области; иначе no-op-копия.
    """
    out = img.copy()
    if color is not None:
        ImageDraw.Draw(out).rectangle(box, fill=color)
    elif blur:
        region = out.crop(box).filter(ImageFilter.GaussianBlur(radius=12))
        out.paste(region, box)
    return out


def to_format(src: str | Path, fmt: str, *, quality: int = 95) -> Path:
    """Конвертировать файл в другой формат (png↔jpg↔webp), вернуть путь нового файла."""
    img = load(src)
    out = Path(src).expanduser().with_suffix("." + fmt.lower().lstrip("."))
    return save(img, out, quality=quality)
