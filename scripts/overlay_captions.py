#!/usr/bin/env python3
"""Накладывает текст-баннеры (верх/низ) на вертикальное видео 9:16 как PNG-оверлей.

Текст рендерится в Pillow (обводка, тень, полупрозрачная плашка, supersampling 2x —
дизайнерское качество, контролируемый текст без AI-gibberish), затем ffmpeg overlay.
Универсально для серии видео-креативов.

Пример:
  python scripts/overlay_captions.py --in raw/VID003.mp4 --out raw/VID003_cap.mp4 \
    --top "I deposit just GHS 10" --bottom "GHS 10 = 20 FREE BETS|on Aviator"
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SS = 2  # supersampling для гладких краёв
ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ARIAL_BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size * SS)


def _text_w(draw, text, font):
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0], b[3] - b[1]


def _draw_centered(draw, cx, y, text, font, fill, stroke_fill, stroke_w, shadow=True):
    w, h = _text_w(draw, text, font)
    x = cx - w // 2
    if shadow:
        draw.text((x + 3 * SS, y + 3 * SS), text, font=font, fill=(0, 0, 0, 160))
    draw.text((x, y), text, font=font, fill=fill,
              stroke_width=stroke_w * SS, stroke_fill=stroke_fill)
    return h


def build_overlay(w, h, top_text, bottom_lines, top_y=0.045, bottom_y=0.80,
                  cover=False, full_width=False):
    """RGBA-оверлей размером видео. bottom_lines — список строк (1-2).

    cover=True — непрозрачные плашки (alpha 240) для перекрытия уже вжатого
    в видео AI-текста. full_width=True — плашки во всю ширину (надёжнее прячут).
    top_y/bottom_y — вертикальная позиция плашек как доля высоты.
    """
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = w * SS // 2
    top_a = 255 if cover else 110
    bot_a = 255 if cover else 210

    # --- ВЕРХ: белый bold + чёрная обводка на тёмной плашке ---
    if top_text:
        f_top = _font(ARIAL_BOLD, 40)
        tw, th = _text_w(d, top_text, f_top)
        pad = 22 * SS
        if cover and full_width:
            # плашка ОТ верхнего края кадра до top_y (надёжно прячет вжатую сверху
            # обвязку — браузерную строку/табы), текст центрируем по вертикали
            plate_bottom = max(int(h * SS * top_y), th + 36 * SS)
            d.rectangle([0, 0, w * SS, plate_bottom], fill=(0, 0, 0, top_a))
            ty = (plate_bottom - th) // 2
            _draw_centered(d, cx, ty, top_text, f_top,
                           fill=(255, 255, 255, 255), stroke_fill=(0, 0, 0, 255), stroke_w=2)
        else:
            by0 = int(h * SS * top_y)
            if full_width:
                box = [0, max(0, by0 - 16 * SS), w * SS, by0 + th + 28 * SS]
            else:
                box = [cx - tw // 2 - pad, by0 - 10 * SS, cx + tw // 2 + pad, by0 + th + 24 * SS]
            d.rounded_rectangle(box, radius=(0 if full_width else 18 * SS), fill=(0, 0, 0, top_a))
            _draw_centered(d, cx, by0, top_text, f_top,
                           fill=(255, 255, 255, 255), stroke_fill=(0, 0, 0, 255), stroke_w=2)

    # --- НИЗ: жёлтый Arial Black на тёмной плашке ---
    if bottom_lines:
        f_bot = _font(ARIAL_BLACK, 46)
        sizes = [_text_w(d, ln, f_bot) for ln in bottom_lines]
        line_h = max(s[1] for s in sizes)
        gap = 12 * SS
        block_h = len(bottom_lines) * line_h + (len(bottom_lines) - 1) * gap
        max_w = max(s[0] for s in sizes)
        pad_x, pad_y = 40 * SS, 28 * SS
        plate_w = w * SS if full_width else max_w + 2 * pad_x
        y_plate0 = int(h * SS * bottom_y)
        x0 = 0 if full_width else cx - plate_w // 2
        x1 = w * SS if full_width else cx + plate_w // 2
        # в cover-режиме тянем плашку до низа кадра (надёжно прячет вжатый баннер),
        # текст центрируем по вертикали внутри полосы
        plate_bottom = (h * SS) if (cover and full_width) else (y_plate0 + block_h + 2 * pad_y)
        d.rounded_rectangle(
            [x0, y_plate0, x1, plate_bottom],
            radius=(0 if full_width else 22 * SS), fill=(8, 8, 8, bot_a))
        if cover and full_width:
            y = y_plate0 + ((plate_bottom - y_plate0) - block_h) // 2
        else:
            y = y_plate0 + pad_y
        for ln in bottom_lines:
            _draw_centered(d, cx, y, ln, f_bot,
                           fill=(255, 211, 0, 255), stroke_fill=(0, 0, 0, 255),
                           stroke_w=2, shadow=False)
            y += line_h + gap

    return img.resize((w, h), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--top", default="")
    ap.add_argument("--bottom", default="", help="строки через | ")
    ap.add_argument("--top-y", type=float, default=0.045)
    ap.add_argument("--bottom-y", type=float, default=0.80)
    ap.add_argument("--cover", action="store_true", help="непрозрачные плашки (перекрыть вжатый текст)")
    ap.add_argument("--full-width", action="store_true", help="плашки во всю ширину")
    args = ap.parse_args()

    # размеры видео
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", args.inp],
        capture_output=True, text=True, check=True)
    w, h = (int(x) for x in probe.stdout.strip().split("x"))

    bottom_lines = [s for s in args.bottom.split("|") if s] if args.bottom else []
    overlay = build_overlay(w, h, args.top, bottom_lines,
                            top_y=args.top_y, bottom_y=args.bottom_y,
                            cover=args.cover, full_width=args.full_width)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        png = tf.name
    overlay.save(png)

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", args.inp, "-i", png,
         "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
         "-c:a", "copy", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
         args.out],
        check=True)
    print(f"OK -> {args.out}  ({w}x{h}, overlay png {png})")


if __name__ == "__main__":
    main()
