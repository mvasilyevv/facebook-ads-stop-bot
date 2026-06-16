# -*- coding: utf-8 -*-
"""core.imaging — детерминированный «ручной фотошоп» (Pillow), без AI-разброса.

Точные операции с пиксельной предсказуемостью: crop/resize под форматы (PWA/FB),
чистый текст-оверлей, композитинг слоёв, цветокор, заливка/блюр зоны (вотермарка),
удаление фона (rembg, опционально). Дополняет AI-правки `core.syntx` (Тир 1):
syntx — «поменять по смыслу», imaging — «точно до пикселя».
"""

from __future__ import annotations

from core.imaging.ops import (
    adjust,
    composite,
    cover_region,
    crop_to_aspect,
    load,
    overlay_text,
    resize_exact,
    save,
    to_format,
)

__all__ = [
    "load",
    "save",
    "resize_exact",
    "crop_to_aspect",
    "overlay_text",
    "composite",
    "adjust",
    "cover_region",
    "to_format",
    "remove_background",
]


def remove_background(*args, **kwargs):  # noqa: ANN002, ANN003 — тонкий ленивый прокси
    """Ленивый прокси к core.imaging.background.remove_background (rembg опционален)."""
    from core.imaging.background import remove_background as _impl

    return _impl(*args, **kwargs)
