# -*- coding: utf-8 -*-
"""Наложение текста/PNG на чистое видео (пайплайн clean-video, см. video-gen.md).

Быстрый режим (пресеты):
    python scripts/overlay_video.py in.mp4 out.mp4 \
        --top "I deposit just GHS 10" \
        --banner "GHS 10 -> 20 FREE BETS on Aviator" \
        --cta "Tap to play now" --cta-start 1.5

Полный контроль (JSON-спека):
    python scripts/overlay_video.py in.mp4 out.mp4 --spec spec.json

Формат spec.json:
    {
      "font_file": "assets/fonts/overlay.ttf",        // опционально
      "overlays": [
        {"preset": "caption", "text": "I deposit just GHS 10"},
        {"preset": "banner",  "text": "GHS 10 -> 20 FREE BETS on Aviator"},
        {"preset": "cta",     "text": "Tap to play now", "start": 1.5},
        {"text": "custom", "y": 400, "fontsize": 36, "box": true},   // без пресета
        {"type": "png", "file": "plate.png", "x": 40, "y": 60, "start": 2}
      ]
    }

⚠️ Эмодзи и спецглифы drawtext не рендерит (или рендерит тофу) — для них PNG-слой.
После наложения ОБЯЗАТЕЛЕН самоотсмотр кадров (ffmpeg -vf fps=1) — как для любого генерата.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.creatives.video_overlay import (
    OverlaySpec,
    OverlayValidationError,
    banner,
    caption,
    cta,
    overlay_video,
    spec_from_dict,
)

# Символы, для которых в популярных шрифтах часто нет глифов.
_RISKY_CHARS = "→←↑↓—«»😀😅🎰💰"


def _parse_args() -> argparse.Namespace:
    """Разбирает аргументы командной строки."""
    parser = argparse.ArgumentParser(description="Оверлеи на чистое видео (ffmpeg drawtext)")
    parser.add_argument("source", help="Входной mp4 (чистый, без текста)")
    parser.add_argument("output", help="Выходной mp4")
    parser.add_argument("--spec", default=None, help="JSON-спека (полный контроль)")
    parser.add_argument("--top", default=None, help="Текст верхнего кэпшена (пресет caption)")
    parser.add_argument("--banner", default=None, help="Текст нижнего баннера (пресет banner)")
    parser.add_argument("--cta", default=None, help="Текст CTA-плашки (пресет cta)")
    parser.add_argument("--cta-start", type=float, default=1.5, help="Секунда появления CTA")
    parser.add_argument("--font", default=None, help="Путь к TTF (иначе автопоиск)")
    return parser.parse_args()


def _build_spec(args: argparse.Namespace) -> OverlaySpec:
    """Собирает спеку из --spec или из быстрых пресетов."""
    if args.spec:
        payload = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        spec = spec_from_dict(payload, base_dir=Path(args.spec).resolve().parent)
        if args.font:
            spec = OverlaySpec(texts=spec.texts, pngs=spec.pngs, font_file=Path(args.font))
        return spec
    texts = []
    if args.top:
        texts.append(caption(args.top))
    if args.banner:
        texts.append(banner(args.banner))
    if args.cta:
        texts.append(cta(args.cta, start=args.cta_start))
    return OverlaySpec(texts=texts, font_file=Path(args.font) if args.font else None)


def _warn_risky_glyphs(spec: OverlaySpec) -> None:
    """Предупреждает о символах, которых может не быть в шрифте."""
    risky = {ch for item in spec.texts for ch in item.text if ch in _RISKY_CHARS}
    if risky:
        print(
            f"⚠️ В тексте есть символы {''.join(sorted(risky))} — проверь глифы на кадре "
            "(у Poppins нет «→», эмодзи — только PNG-слоем)",
            file=sys.stderr,
        )


async def _run(args: argparse.Namespace, source: Path, output: Path) -> int:
    try:
        spec = _build_spec(args)
        _warn_risky_glyphs(spec)
        await overlay_video(source, output, spec)
    except (OverlayValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    print(f"Готово: {args.output}")
    print("Самоотсмотр: ffmpeg -i out.mp4 -vf fps=1 frames/f_%02d.jpg — проверь текст по кадрам.")
    return 0


def main() -> int:
    """Синхронная обвязка CLI."""
    args = _parse_args()
    source = Path(args.source).expanduser()
    output = Path(args.output).expanduser()
    return asyncio.run(_run(args, source, output))


if __name__ == "__main__":
    raise SystemExit(main())
