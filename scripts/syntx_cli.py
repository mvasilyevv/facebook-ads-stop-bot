#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI для core.syntx — генерация креативов через прямой API syntx.ai (без UI).

Подкоманды:
  balance                      — баланс токенов + срок жизни токена
  models [--scope image]       — каталог моделей (ai_name / model_type / default / медиа)
  cost --ai X --model Y [--param k=v]  — цена генерации в токенах
  image --prompt ... [--ref f]* --out f [--variants N] [--crop WxH]  — генерация картинок

Токен: --token, env SYNTX_AUTH_TOKEN или строка SYNTX_AUTH_TOKEN=... в .env.

Примеры:
  python scripts/syntx_cli.py balance
  python scripts/syntx_cli.py models --scope image
  python scripts/syntx_cli.py image --prompt "..." --ref ref.jpg \
      --ai sora-images --model gpt-image-2 --ar 16:9 --detail high \
      --out data/syntx_out/X/feature.jpg --crop 1024x500
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

# запуск как `python scripts/syntx_cli.py` из корня репозитория
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.syntx import GenRequest, SyntxClient, token_days_left  # noqa: E402
from core.syntx.auth import resolve_syntx_token  # noqa: E402
from core.syntx.errors import SyntxError  # noqa: E402


async def _cmd_balance(args: argparse.Namespace) -> int:
    async with SyntxClient(token=args.token) as cl:
        bal = await cl.get_balance()
        days = token_days_left(resolve_syntx_token(args.token))
        days_s = f"{days:.0f} дн" if days is not None else "?"
        print(f"Баланс: {bal.tokens} токенов · токен живёт ещё {days_s}")
    return 0


async def _cmd_models(args: argparse.Namespace) -> int:
    async with SyntxClient(token=args.token) as cl:
        cat = await cl.list_models()
    rows = cat.for_scope(args.scope) if args.scope else cat.models
    if not rows:
        print("Моделей не найдено")
        return 0
    print(f"{'ai_name':<18} {'model_type':<26} {'def':<4} media")
    for m in rows:
        media = ",".join(m.allowed_media_types) or "-"
        print(f"{m.ai_name:<18} {m.model_type:<26} {'*' if m.default else ' ':<4} {media}")
    print(f"\nВсего: {len(rows)}")
    return 0


async def _cmd_cost(args: argparse.Namespace) -> int:
    extra = dict(p.split("=", 1) for p in (args.param or []))
    async with SyntxClient(token=args.token) as cl:
        cost = await cl.get_cost(args.ai, args.model, **extra)
    if cost is None:
        print("Цена недоступна (модель требует доп. cost-параметры: --param k=v)")
        return 1
    print(f"{args.ai}/{args.model}: {cost} токенов/генерация")
    return 0


def _crop(path: Path, spec: str) -> None:
    """Центр-кроп под WxH через sips (macOS): сначала по соотношению, потом ресайз."""
    w, h = (int(x) for x in spec.lower().split("x"))
    # подобрать промежуточный кроп под соотношение, затем ресайз в точный размер
    ratio = w / h
    out = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    cur = [int(s.split(":")[1]) for s in out.splitlines() if "pixel" in s]
    cw, ch = cur[0], cur[1]
    if cw / ch > ratio:
        crop_h, crop_w = ch, int(ch * ratio)
    else:
        crop_w, crop_h = cw, int(cw / ratio)
    subprocess.run(
        ["sips", "-c", str(crop_h), str(crop_w), str(path)], capture_output=True, check=True
    )
    subprocess.run(["sips", "-z", str(h), str(w), str(path)], capture_output=True, check=True)


def _expand(p: str) -> Path:
    """Sync: раскрыть ~ в пути (вне async-тела, чтобы не цеплять ASYNC240)."""
    return Path(p).expanduser()


async def _cmd_image(args: argparse.Namespace) -> int:
    out_base = _expand(args.out)
    saved: list[Path] = []
    async with SyntxClient(token=args.token) as cl:
        for v in range(args.variants):
            req = GenRequest(
                scope="image",
                ai_name=args.ai,
                model_type=args.model,
                prompt=args.prompt,
                image_refs=tuple(args.ref or []),
                aspect_ratio=args.ar,
                quality=args.quality,
                details_quality=args.detail,
                n=args.n,
            )
            target = (
                out_base
                if args.variants == 1
                else out_base.with_name(f"{out_base.stem}_v{v + 1}{out_base.suffix}")
            )
            res = await cl.generate_image(req, download_to=target)
            for p in res.local_paths:
                if args.crop:
                    _crop(p, args.crop)
                saved.append(p)
                print(f"[ok] {p}  (~{res.tokens_spent} токенов)")
    if not saved:
        print("Ничего не сгенерировано", file=sys.stderr)
        return 1
    print("\nГотово:")
    for p in saved:
        print(" ", p)
    return 0


async def _cmd_edit(args: argparse.Namespace) -> int:
    out_base = _expand(args.out)
    saved: list[Path] = []
    async with SyntxClient(token=args.token) as cl:
        for v in range(args.variants):
            target = (
                out_base
                if args.variants == 1
                else out_base.with_name(f"{out_base.stem}_v{v + 1}{out_base.suffix}")
            )
            res = await cl.edit_image(
                args.image,
                args.prompt,
                mask=args.mask,
                ai_name=args.ai,
                model_type=args.model,
                image_size=args.image_size,
                download_to=target,
            )
            for p in res.local_paths:
                if args.crop:
                    _crop(p, args.crop)
                saved.append(p)
                print(f"[ok] {p}  (~{res.tokens_spent} токенов)")
    if not saved:
        print("Ничего не сгенерировано", file=sys.stderr)
        return 1
    print("\nГотово:")
    for p in saved:
        print(" ", p)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="syntx.ai CLI (прямой API)")
    ap.add_argument("--token", default=None, help="JWT (иначе env/.env)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("balance")

    p_models = sub.add_parser("models")
    p_models.add_argument("--scope", default=None, help="image|video|audio|text")

    p_cost = sub.add_parser("cost")
    p_cost.add_argument("--ai", required=True)
    p_cost.add_argument("--model", required=True)
    p_cost.add_argument("--param", action="append", help="cost-параметр k=v (повторяемый)")

    p_img = sub.add_parser("image")
    p_img.add_argument("--prompt", required=True)
    p_img.add_argument(
        "--ref", action="append", default=[], help="референс (повторяемый, до лимита модели)"
    )
    p_img.add_argument("--ai", default="sora-images")
    p_img.add_argument("--model", default="gpt-image-2")
    p_img.add_argument("--ar", default="16:9")
    p_img.add_argument("--quality", default="1K")
    p_img.add_argument("--detail", default="medium", choices=["low", "medium", "high"])
    p_img.add_argument("--n", type=int, default=1)
    p_img.add_argument("--variants", type=int, default=1)
    p_img.add_argument("--out", required=True)
    p_img.add_argument(
        "--crop", default=None, help="центр-кроп под WxH (sips, macOS), напр. 1024x500"
    )

    p_edit = sub.add_parser("edit")
    p_edit.add_argument("--image", required=True, help="исходник (путь или r2-url)")
    p_edit.add_argument("--prompt", required=True, help="инструкция правки")
    p_edit.add_argument("--mask", default=None, help="маска для inpaint (экспериментально)")
    p_edit.add_argument("--ai", default=None, help="по умолч. banana (faithful edit)")
    p_edit.add_argument("--model", default=None, help="по умолч. banana3")
    p_edit.add_argument("--image-size", dest="image_size", default="2K")
    p_edit.add_argument("--variants", type=int, default=1)
    p_edit.add_argument("--out", required=True)
    p_edit.add_argument("--crop", default=None, help="центр-кроп под WxH (sips), напр. 1024x500")

    args = ap.parse_args()
    handlers = {
        "balance": _cmd_balance,
        "models": _cmd_models,
        "cost": _cmd_cost,
        "image": _cmd_image,
        "edit": _cmd_edit,
    }
    try:
        return asyncio.run(handlers[args.cmd](args))
    except SyntxError as exc:
        print(f"Ошибка syntx: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
