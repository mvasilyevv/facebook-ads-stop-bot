# -*- coding: utf-8 -*-
"""Почти незаметная пиксельная уникализация изображений."""

from __future__ import annotations

import hashlib
import io
import random

from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError


class CreativeImageError(ValueError):
    """Ошибка чтения или обработки изображения."""


def _seed_to_random(seed_text: str) -> random.Random:
    """Создаёт стабильный генератор параметров для одной копии."""
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Переводит изображение в RGB без сохранения альфа-канала."""
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def _micro_resample(image: Image.Image, rnd: random.Random) -> Image.Image:
    """Делает микрокадрирование с возвратом к исходному размеру."""
    width, height = image.size
    if width < 64 or height < 64:
        return image

    side = rnd.choice(("left", "right", "top", "bottom", "horizontal", "vertical"))
    left = 1 if side in {"left", "horizontal"} else 0
    right = 1 if side in {"right", "horizontal"} else 0
    top = 1 if side in {"top", "vertical"} else 0
    bottom = 1 if side in {"bottom", "vertical"} else 0

    cropped = image.crop((left, top, width - right, height - bottom))
    return cropped.resize((width, height), Image.Resampling.LANCZOS)


def _apply_tone_shift(image: Image.Image, rnd: random.Random) -> Image.Image:
    """Применяет очень слабые отличия тона и детализации."""
    brightness = 1.0 + rnd.uniform(-0.0035, 0.0035)
    contrast = 1.0 + rnd.uniform(-0.004, 0.004)
    color = 1.0 + rnd.uniform(-0.005, 0.005)
    sharpness = 1.0 + rnd.uniform(-0.003, 0.003)

    image = ImageEnhance.Brightness(image).enhance(brightness)
    image = ImageEnhance.Contrast(image).enhance(contrast)
    image = ImageEnhance.Color(image).enhance(color)
    return ImageEnhance.Sharpness(image).enhance(sharpness)


def _apply_subtle_noise(image: Image.Image, rnd: random.Random) -> Image.Image:
    """Добавляет шум ниже заметного визуального порога."""
    sigma = rnd.uniform(1.0, 1.8)
    alpha = rnd.uniform(0.0015, 0.003)
    noise = Image.effect_noise(image.size, sigma).convert("RGB")
    return Image.blend(image, noise, alpha)


def uniquify_image_bytes(
    source_bytes: bytes,
    *,
    source_name: str,
    copy_index: int,
    creative_index: int,
    run_slug: str,
) -> bytes:
    """Возвращает уникализированное изображение в JPEG без EXIF."""
    if not source_bytes:
        raise CreativeImageError(f"Файл «{source_name}» пустой")

    try:
        with Image.open(io.BytesIO(source_bytes)) as image:
            image.load()
            base = ImageOps.exif_transpose(image)
            base = _flatten_to_rgb(base)
    except UnidentifiedImageError as exc:
        raise CreativeImageError(
            f"Файл «{source_name}» не удалось прочитать как изображение"
        ) from exc
    except OSError as exc:
        raise CreativeImageError(
            f"Файл «{source_name}» повреждён или имеет неподдерживаемый формат"
        ) from exc

    if base.width < 8 or base.height < 8:
        raise CreativeImageError(f"Изображение «{source_name}» слишком маленькое для обработки")

    rnd = _seed_to_random(f"{run_slug}:{source_name}:{creative_index}:{copy_index}")
    result = _micro_resample(base, rnd)
    result = _apply_tone_shift(result, rnd)
    result = _apply_subtle_noise(result, rnd)

    output = io.BytesIO()
    result.save(
        output,
        format="JPEG",
        quality=96,
        optimize=True,
        progressive=True,
        subsampling=0,
    )
    return output.getvalue()
