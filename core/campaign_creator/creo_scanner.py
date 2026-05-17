# -*- coding: utf-8 -*-
"""Сканер папки с креативами для билдинга AdsetSpec.

Структура папки:
    creo_folder/
        1/             ← адсет №1 (имя подпапки = число)
            CR004_1.jpeg
            CR004_2.jpeg
        2/             ← адсет №2
            CR005_1.mp4
        ...

Подпапки с нечисловыми именами игнорируются. Файлы с не-медийными
расширениями фильтруются. Сортировка адсетов — по числовому имени папки,
файлов — лексикографически.
"""

from __future__ import annotations

from pathlib import Path

from core.campaign_creator.plan_types import AdsetSpec

ALLOWED_EXTS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".mp4", ".mov", ".gif"})


def scan_creo_folder(
    creo_folder: str | Path,
    *,
    name_suffix: str = "",
    headline: str = "",
    primary_text: str = "",
    description: str = "",
) -> list[AdsetSpec]:
    """Просканировать creo_folder и вернуть list[AdsetSpec].

    name_suffix/headline/primary_text/description применяются ко всем адсетам
    одинаково — конкретные тексты per-adset задаются позже, если нужно.

    Бросает ValueError, если папка не существует или в ней нет валидных адсетов.
    """
    root = Path(creo_folder)
    if not root.exists() or not root.is_dir():
        raise ValueError(f"creo_folder не найден или не папка: {root}")

    numbered: list[tuple[int, Path]] = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        try:
            idx = int(sub.name)
        except ValueError:
            continue
        numbered.append((idx, sub))

    if not numbered:
        raise ValueError(f"В {root} нет подпапок с числовыми именами")

    numbered.sort(key=lambda x: x[0])

    adsets: list[AdsetSpec] = []
    for _, sub in numbered:
        files = sorted(
            p.name for p in sub.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_EXTS
        )
        if not files:
            continue
        adsets.append(
            AdsetSpec(
                name_suffix=name_suffix,
                creo_subfolder=sub.name,
                headline=headline,
                primary_text=primary_text,
                description=description,
                creatives=files,
            )
        )

    if not adsets:
        raise ValueError(f"В {root} не найдено ни одного адсета с валидными креативами")

    return adsets
