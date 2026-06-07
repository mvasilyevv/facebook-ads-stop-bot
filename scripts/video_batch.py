# -*- coding: utf-8 -*-
"""Video Batch — мост реестр → Remotion → uniquify (Волна 2 контент-пайплайна).

Берёт креативы из Creative Registry (docs/creatives), собирает пропсы для Remotion-
шаблона, рендерит пакетом через remotion/scripts/render-batch.mjs (текст детерминирован,
без AI-gibberish), затем прогоняет результат через core.creatives.video_uniquifier
(3 копии в ~/Documents/FB_Agent_Creo по naming convention).

Тексты-хуки берутся как болванки из creative.angle — доводятся человеком (правка
batch.json или --hook). Один базовый ролик (--bg, чистое видео из syntx) × N текстов.

Запуск (через .venv/bin/python — он шеллит в Node 22 + ffmpeg):
    python scripts/video_batch.py --geo KE --slot CR2 --bg clean.mp4 --codes KE_CR2_CR013
    python scripts/video_batch.py --geo KE --slot CR2 --bg clean.mp4 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.creatives.registry import Creative, Geo, load_registry  # noqa: E402
from core.creatives.video_uniquifier import uniquify_videos  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_CREATIVES = REPO_ROOT / "docs" / "creatives"
RENDER_BATCH = REPO_ROOT / "remotion" / "scripts" / "render-batch.mjs"
RENDER_OUT = REPO_ROOT / "remotion" / "out"


class VideoBatchError(RuntimeError):
    """Ошибка сборки или рендера видео-батча."""


def _resolve_node() -> str:
    """Node для Remotion (нужен 18+): env REMOTION_NODE → node@22/@20 → PATH."""
    env = os.environ.get("REMOTION_NODE")
    if env:
        return env
    for cand in ("/usr/local/opt/node@22/bin/node", "/usr/local/opt/node@20/bin/node"):
        if Path(cand).exists():
            return cand
    found = shutil.which("node")
    if not found:
        raise VideoBatchError("node не найден — нужен Node 18+ для Remotion")
    return found


def parse_amount(value: str) -> tuple[str, str]:
    """«153 KES» → ('153','KES'); «GHS 10» → ('10','GHS'); «1000» → ('1000','')."""
    parts = str(value or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    first, second = parts[0], parts[1]
    if first.replace(",", "").replace(".", "").isdigit():
        return first, second
    if second.replace(",", "").replace(".", "").isdigit():
        return second, first
    return first, second


def build_props(
    creative: Creative,
    geo: Geo,
    *,
    cta_text: str,
    cta_start_sec: float,
    hook_text: str | None = None,
) -> dict:
    """Собирает пропсы Remotion-шаблона из записи реестра (тексты — болванки из angle)."""
    amount, currency = parse_amount(str(geo.payment.get("min_deposit_local", "")))
    lang = geo.languages[0] if geo.languages else "en"
    return {
        "code": creative.code,
        "geo": geo.code,
        "lang": lang,
        "hook": {"text": hook_text or creative.angle or creative.code},
        "offer": {"amount": amount or "0", "currency": currency},
        "cta": {"text": cta_text, "startSec": cta_start_sec},
        "bg": {"type": "solid", "color": "#0b0b0f"},  # render-batch заменит на --bg
    }


def select_creatives(slot, codes: list[str] | None) -> list[Creative]:
    """Выбор креативов: по явным --codes, иначе все format=='video'."""
    if codes:
        wanted = set(codes)
        return [c for c in slot.creatives if c.code in wanted]
    return [c for c in slot.creatives if c.format == "video"]


async def _run_node(args: list[str]) -> None:
    """Запускает render-batch.mjs (Node) с прозрачным stdout/stderr."""
    node = _resolve_node()
    try:
        process = await asyncio.create_subprocess_exec(node, str(RENDER_BATCH), *args)
    except FileNotFoundError as exc:
        raise VideoBatchError(f"не удалось запустить node ({node})") from exc
    rc = await process.wait()
    if rc != 0:
        raise VideoBatchError(f"render-batch.mjs завершился с кодом {rc}")


async def run(
    *,
    geo_code: str,
    slot_code: str,
    bg: str,
    codes: list[str] | None,
    cta_text: str,
    cta_start_sec: float,
    fmt: str,
    copies: int,
    hook_text: str | None,
    dry_run: bool,
    uniquify: bool,
) -> int:
    """Основной поток: реестр → props → render → uniquify. Возвращает число роликов."""
    reg = load_registry(DOCS_CREATIVES)
    geo = reg.geos.get(geo_code)
    if geo is None:
        raise VideoBatchError(f"гео {geo_code!r} не найдено в реестре")
    slot = geo.slots.get(slot_code)
    if slot is None:
        raise VideoBatchError(f"слот {slot_code!r} не найден в гео {geo_code}")

    creatives = select_creatives(slot, codes)
    if not creatives:
        raise VideoBatchError(
            "нет креативов: укажи --codes или заведи creative с format=video в слоте"
        )

    items = [
        build_props(c, geo, cta_text=cta_text, cta_start_sec=cta_start_sec, hook_text=hook_text)
        for c in creatives
    ]
    print(f"Креативов: {len(items)} ({', '.join(c.code for c in creatives)})")
    for it in items:
        print(f"  {it['code']}: hook={it['hook']['text']!r} offer={it['offer']}")

    if dry_run:
        print("\n[dry-run] рендера не было.")
        return len(items)

    if not bg:
        raise VideoBatchError("--bg обязателен (чистое базовое видео из syntx)")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False)
        batch_file = fh.name

    try:
        await _run_node(
            ["--input", batch_file, "--out", str(RENDER_OUT), "--bg", bg, "--format", fmt]
        )
    finally:
        os.unlink(batch_file)

    rendered = [RENDER_OUT / f"{c.code}_{fmt}.mp4" for c in creatives]
    missing = [p for p in rendered if not p.exists()]
    if missing:
        raise VideoBatchError(f"не отрендерились: {', '.join(p.name for p in missing)}")
    print(f"\n✅ отрендерено {len(rendered)} → {RENDER_OUT}")

    if uniquify:
        result = await uniquify_videos(
            offer_name=f"{geo_code}_{slot_code}_video",
            copies=copies,
            videos=rendered,
        )
        print(f"✅ uniquify: {result.video_count}×{result.copy_count} → {result.iteration_dir}")
    return len(rendered)


def main() -> None:
    parser = argparse.ArgumentParser(description="Video Batch: реестр → Remotion → uniquify")
    parser.add_argument("--geo", required=True, help="код гео (напр. KE)")
    parser.add_argument("--slot", required=True, help="код слота (напр. CR2)")
    parser.add_argument("--bg", default="", help="чистое базовое видео (из syntx)")
    parser.add_argument(
        "--codes", default="", help="коды креативов через запятую (иначе все video)"
    )
    parser.add_argument("--cta", default="PLAY NOW", help="текст CTA")
    parser.add_argument("--cta-start", type=float, default=3.0, help="секунда появления CTA")
    parser.add_argument("--hook", default=None, help="переопределить текст хука для всех")
    parser.add_argument("--format", default="9x16", choices=("9x16", "1x1", "16x9"))
    parser.add_argument("--copies", type=int, default=3, help="копий на ролик (uniquify)")
    parser.add_argument("--dry-run", action="store_true", help="показать props без рендера")
    parser.add_argument("--no-uniquify", action="store_true", help="не делать uniquify")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or None
    asyncio.run(
        run(
            geo_code=args.geo,
            slot_code=args.slot,
            bg=args.bg,
            codes=codes,
            cta_text=args.cta,
            cta_start_sec=args.cta_start,
            fmt=args.format,
            copies=args.copies,
            hook_text=args.hook,
            dry_run=args.dry_run,
            uniquify=not args.no_uniquify,
        )
    )


if __name__ == "__main__":
    main()
