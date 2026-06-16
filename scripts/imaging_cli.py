#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI для core.imaging — детерминированный «ручной фотошоп» (Pillow + rembg).

Подкоманды:
  crop     --in f --out f --size WxH         центр-кроп под формат (PWA/FB)
  resize   --in f --out f --size WxH         точный ресайз
  text     --in f --out f --text "…" --xy X,Y [--size N --color #fff --anchor mm --stroke N]
  composite --bg f --fg f --out f [--xy X,Y --scale F]   наложить слой (с альфой)
  color    --in f --out f [--brightness --contrast --saturation --sharpness]
  cover    --in f --out f --box L,T,R,B [--color #000 | --blur]   закрыть зону (вотермарка)
  rmbg     --in f --out f [--model u2net]    удалить фон (rembg, PNG с альфой)
  format   --in f --fmt png|jpg|webp         конвертация формата

Пример:
  python scripts/imaging_cli.py crop --in raw.png --out feature.jpg --size 1024x500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import imaging  # noqa: E402


def _wh(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


def _xy(s: str) -> tuple[int, int]:
    x, y = s.split(",")
    return int(x), int(y)


def main() -> int:
    ap = argparse.ArgumentParser(description="core.imaging CLI (Pillow)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("crop")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--size", required=True, help="WxH, напр. 1024x500")

    p = sub.add_parser("resize")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--size", required=True)

    p = sub.add_parser("text")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--xy", required=True, help="X,Y")
    p.add_argument("--size", type=int, default=48)
    p.add_argument("--color", default="#FFFFFF")
    p.add_argument("--font", default=None)
    p.add_argument("--anchor", default="la")
    p.add_argument("--stroke", type=int, default=0)

    p = sub.add_parser("composite")
    p.add_argument("--bg", required=True)
    p.add_argument("--fg", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--xy", default="0,0")
    p.add_argument("--scale", type=float, default=1.0)

    p = sub.add_parser("color")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--brightness", type=float, default=1.0)
    p.add_argument("--contrast", type=float, default=1.0)
    p.add_argument("--saturation", type=float, default=1.0)
    p.add_argument("--sharpness", type=float, default=1.0)

    p = sub.add_parser("cover")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--box", required=True, help="L,T,R,B")
    p.add_argument("--color", default=None)
    p.add_argument("--blur", action="store_true")

    p = sub.add_parser("rmbg")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--model", default="u2net")

    p = sub.add_parser("format")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--fmt", required=True)

    a = ap.parse_args()
    if a.cmd == "crop":
        imaging.save(imaging.crop_to_aspect(imaging.load(a.inp), *_wh(a.size)), a.out)
    elif a.cmd == "resize":
        imaging.save(imaging.resize_exact(imaging.load(a.inp), *_wh(a.size)), a.out)
    elif a.cmd == "text":
        img = imaging.overlay_text(
            imaging.load(a.inp),
            a.text,
            xy=_xy(a.xy),
            size=a.size,
            color=a.color,
            font_path=a.font,
            anchor=a.anchor,
            stroke_width=a.stroke,
        )
        imaging.save(img, a.out)
    elif a.cmd == "composite":
        out = imaging.composite(imaging.load(a.bg), imaging.load(a.fg), xy=_xy(a.xy), scale=a.scale)
        imaging.save(out, a.out)
    elif a.cmd == "color":
        out = imaging.adjust(
            imaging.load(a.inp),
            brightness=a.brightness,
            contrast=a.contrast,
            color=a.saturation,
            sharpness=a.sharpness,
        )
        imaging.save(out, a.out)
    elif a.cmd == "cover":
        box = tuple(int(x) for x in a.box.split(","))
        imaging.save(
            imaging.cover_region(imaging.load(a.inp), box, color=a.color, blur=a.blur), a.out
        )
    elif a.cmd == "rmbg":
        imaging.remove_background(a.inp, a.out, model=a.model)
    elif a.cmd == "format":
        imaging.to_format(a.inp, a.fmt)
    print(f"[ok] {a.cmd} → {getattr(a, 'out', a.inp)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
