# -*- coding: utf-8 -*-
"""Уникализатор видео-креативов: новый md5 + невидимые пиксельные микро-изменения.

Раскладка как у картиночного uniquify: `<root>/<offer>_<ts>_..._<N>copies/{1..N}/`.
Требует ffmpeg/ffprobe в PATH (brew install ffmpeg).

    python scripts/uniquify_video.py creo1.mp4 creo2.mp4 --offer GH_AVI --copies 3
    python scripts/uniquify_video.py creo.mp4 --offer GH_AVI --copies 3 --out /tmp/creo
    python scripts/uniquify_video.py creo.mp4 --offer GH_AVI --no-speed   # без сдвига скорости
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.creatives.service import CreativeValidationError
from core.creatives.video_uniquifier import VideoUniquifyError, uniquify_videos


def _parse_args() -> argparse.Namespace:
    """Разбирает аргументы командной строки."""
    parser = argparse.ArgumentParser(description="Уникализация видео-креативов (ffmpeg)")
    parser.add_argument("videos", nargs="+", help="Пути к исходным видео (mp4/mov)")
    parser.add_argument("--offer", required=True, help="Код оффера, напр. GH_AVI")
    parser.add_argument("--copies", type=int, default=3, help="Число копий (дефолт 3)")
    parser.add_argument("--out", default=None, help="Корневая папка (дефолт ~/Documents/FB_Agent_Creo)")
    parser.add_argument(
        "--no-speed",
        action="store_true",
        help="Не трогать скорость/длительность (только кроп/тон/шум/перекодирование)",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace, videos: list[Path], base_dir: Path | None) -> int:
    try:
        result = await uniquify_videos(
            offer_name=args.offer,
            copies=args.copies,
            videos=videos,
            base_dir=base_dir,
            speed_jitter=not args.no_speed,
        )
    except (CreativeValidationError, VideoUniquifyError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    print(f"Готово: {result.video_count} видео × {result.copy_count} копий")
    print(f"Папка: {result.iteration_dir}")
    for file in result.files:
        print(f"  [{file.copy_index}] {file.output_name}  md5={file.md5}")
    return 0


def main() -> int:
    """Синхронная обвязка: разбор аргументов и запуск event loop."""
    args = _parse_args()
    videos = [Path(v).expanduser() for v in args.videos]
    base_dir = Path(args.out).expanduser() if args.out else None
    return asyncio.run(_run(args, videos, base_dir))


if __name__ == "__main__":
    raise SystemExit(main())
