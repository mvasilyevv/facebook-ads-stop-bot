#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мульти-модельный анализ креативов PWA (только анализ, без генерации).

Берёт картинки PWA-папки → каждую прогоняет через пул text-vision моделей разных
лабораторий (GPT-5.5 / Gemini-3.1-Pro / Grok-4.3) с gambling-freedom промптом →
единый строгий JSON у всех → печатает сравнительную таблицу + пишет отчёт.

Токен: env SYNTX_AUTH_TOKEN / .env.

Пример:
  python scripts/analyze_pwa.py \
    --folder "~/Downloads/scan_GH_Android (1)" --offer "Chicken Road" --geo Ghana \
    --archetype InOut --images "...6a283a....jpg" --images "...0x720.png" \
    --out data/syntx_out/analysis_GH_CR.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from core.syntx import SyntxClient, build_analysis_prompt  # noqa: E402
from core.syntx.analysis import DEFAULT_ANALYSIS_POOL  # noqa: E402

_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _role_for(path: Path) -> str:
    """Грубо определить роль ассета по пропорциям (для контекста промпта)."""
    try:
        with Image.open(path) as im:
            w, h = im.size
    except OSError:
        return "store listing image"
    ratio = w / h if h else 1.0
    if 0.8 <= ratio <= 1.25:
        return "app icon (square)"
    if h > w:
        return "vertical store screenshot (in-game UI)"
    return "horizontal feature graphic / banner"


def _pick_images(folder: Path, explicit: list[str], limit: int) -> list[Path]:
    if explicit:
        return [Path(p).expanduser() for p in explicit]
    files = sorted(
        p for p in folder.iterdir() if p.suffix.lower() in _IMG_EXT and not p.name.startswith(".")
    )
    # отсечь мелкие аватары (<200px по меньшей стороне)
    out: list[Path] = []
    for p in files:
        try:
            with Image.open(p) as im:
                if min(im.size) >= 200:
                    out.append(p)
        except OSError:
            continue
    return out[:limit]


def _short(s: object, n: int = 60) -> str:
    return (str(s) if s is not None else "")[:n]


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""


def _write_report(out: Path, report: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


async def _run(args: argparse.Namespace) -> int:
    folder = _expand(args.folder)
    listing_path = _expand(args.listing) if args.listing else (folder / "text.txt")
    listing = _read_text(listing_path)
    images = _pick_images(folder, args.images, args.max_images)
    if not images:
        print("Нет картинок для анализа", file=sys.stderr)
        return 1

    report: dict = {"offer": args.offer, "geo": args.geo, "archetype": args.archetype, "images": []}
    async with SyntxClient(token=args.token) as cl:
        for img in images:
            role = _role_for(img)
            prompt = build_analysis_prompt(
                offer=args.offer,
                geo=args.geo,
                archetype=args.archetype,
                image_role=role,
                listing_text=listing,
            )
            results = await cl.analyze_ensemble(str(img), prompt)
            print(f"\n=== {img.name}  [{role}] ===")
            print(f"{'модель':<16} {'вердикт':<12} {'score':<6} топ-проблема")
            for r in results:
                issues = r.parsed.get("issues") or []
                top = _short(
                    issues[0].get("what")
                    if issues and isinstance(issues[0], dict)
                    else (r.error or "—")
                )
                print(f"{r.label:<16} {r.verdict:<12} {str(r.score or '-'):<6} {top}")
            report["images"].append(
                {
                    "file": img.name,
                    "role": role,
                    "results": [
                        {
                            "label": r.label,
                            "ai_name": r.ai_name,
                            "model_type": r.model_type,
                            "verdict": r.verdict,
                            "score": r.score,
                            "parsed": r.parsed,
                            "error": r.error,
                            "raw": r.raw[:4000],
                        }
                        for r in results
                    ],
                }
            )

    if args.out:
        out = _expand(args.out)
        _write_report(out, report)
        print(f"\nОтчёт: {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Мульти-модельный анализ креативов PWA")
    ap.add_argument("--folder", required=True)
    ap.add_argument("--offer", required=True)
    ap.add_argument("--geo", required=True)
    ap.add_argument("--archetype", default="—")
    ap.add_argument("--listing", default=None, help="по умолч. <folder>/text.txt")
    ap.add_argument(
        "--images", action="append", default=[], help="явный список (иначе авто из folder)"
    )
    ap.add_argument("--max-images", dest="max_images", type=int, default=3)
    ap.add_argument("--out", default=None, help="путь JSON-отчёта")
    ap.add_argument("--token", default=None)
    args = ap.parse_args()
    _ = DEFAULT_ANALYSIS_POOL  # пул по умолчанию (см. core/syntx/analysis.py)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
