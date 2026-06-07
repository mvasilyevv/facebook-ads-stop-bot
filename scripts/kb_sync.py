# -*- coding: utf-8 -*-
"""KB Sync — идемпотентная синхронизация docs/ → NotebookLM (база знаний отдела).

Собирает doc-set из репозитория, конвертит не-markdown (yaml→code-fence, pdf/docx/…→
markitdown), заливает в ноутбук(и) только изменённое (sha256-дельта по манифесту
data/_kb_manifest.json). Защита от дублей: повторный прогон = 0 заливок.

Гибридная раскладка ноутбуков (дефолт per-geo):
  - «FB_Agent KB»        — методология (SOP, playbooks, schema, kb, stop-rules, risk);
  - «FB_Agent KB — <GEO>» — данные гео (geo.yaml, slots/*.yaml, reports/*.md).

Запуск (через .venv/bin/python — он шеллит в 3.12-CLI notebooklm):
    python scripts/kb_sync.py --dry-run            # план без заливки
    python scripts/kb_sync.py                       # синк (per-geo)
    python scripts/kb_sync.py --notebook-mode shared
    python scripts/kb_sync.py --geo GH
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.creatives.registry import load_registry  # noqa: E402
from core.markitdown_adapter import SUPPORTED_SUFFIXES, to_markdown  # noqa: E402
from core.notebooklm import (  # noqa: E402
    KbManifest,
    NotebookLMClient,
    NotebookLMError,
    sha256_of_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
KB_BUILD = REPO_ROOT / "data" / "kb_build"
MANIFEST_PATH = REPO_ROOT / "data" / "_kb_manifest.json"

SHARED_TITLE = "FB_Agent KB"
SHARED = "__shared__"
_DIRECT_SUFFIXES = {".md", ".markdown", ".txt"}
_YAML_SUFFIXES = {".yaml", ".yml"}

# Методология (target=shared). Пути относительно repo root.
METHODOLOGY_FILES = [
    "docs/creative_kb.md",
    "docs/creatives/SOP.md",
    "docs/creatives/_schema.md",
    "docs/creatives/README.md",
    "docs/creatives/hooks.yaml",
    "docs/stop_rules.md",
    "docs/research_risk_framework.md",
    "docs/roadmap/creative-analytics.md",
]


def geo_title(code: str) -> str:
    return f"{SHARED_TITLE} — {code}"


@dataclass(frozen=True)
class DocItem:
    """Файл doc-set: исходник, относительный путь (ключ/тайтл) и целевой ноутбук."""

    abs_path: Path
    rel_path: str
    target: str  # SHARED либо код гео


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def collect_docset(mode: str, geo_filter: str | None) -> list[DocItem]:
    """Собирает список файлов под заливку с привязкой к целевому ноутбуку."""
    items: list[DocItem] = []
    seen: set[str] = set()

    def add(path: Path, target: str) -> None:
        if not path.is_file():
            return
        rel = _rel(path)
        if rel in seen:
            return
        seen.add(rel)
        items.append(DocItem(abs_path=path, rel_path=rel, target=target))

    # Методология
    for rel in METHODOLOGY_FILES:
        add(REPO_ROOT / rel, SHARED)
    for pb in sorted((DOCS / "playbooks").glob("*.md")):
        add(pb, SHARED)

    # Гео-данные (в shared-режиме всё уходит в общий ноутбук)
    reg = load_registry(DOCS / "creatives")
    for code in sorted(reg.geos):
        if geo_filter and code != geo_filter:
            continue
        target = SHARED if mode == "shared" else code
        geo_dir = DOCS / "creatives" / "geo" / code
        add(geo_dir / "geo.yaml", target)
        for slot in sorted((geo_dir / "slots").glob("*.yaml")):
            add(slot, target)
        for report in sorted((geo_dir / "reports").glob("*.md")):
            add(report, target)

    return items


def _yaml_to_md(src: Path, rel: str) -> str:
    """Оборачивает YAML в markdown code-fence (NotebookLM грузит .md, не .yaml)."""
    return f"# {rel}\n\n```yaml\n{src.read_text(encoding='utf-8')}\n```\n"


async def prepare_source(item: DocItem) -> Path | None:
    """Готовит файл к заливке: .md напрямую, .yaml→fence, pdf/docx/…→markitdown."""
    suffix = item.abs_path.suffix.lower()
    if suffix in _DIRECT_SUFFIXES:
        return item.abs_path
    out = KB_BUILD / (item.rel_path + ".md")
    if suffix in _YAML_SUFFIXES:
        await asyncio.to_thread(out.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(out.write_text, _yaml_to_md(item.abs_path, item.rel_path), "utf-8")
        return out
    if suffix in SUPPORTED_SUFFIXES:
        return await to_markdown(item.abs_path, out)
    print(f"  [skip] неподдерживаемый тип: {item.rel_path}")
    return None


def _resolve_title(target: str) -> str:
    return SHARED_TITLE if target == SHARED else geo_title(target)


async def sync(*, mode: str, geo_filter: str | None, dry_run: bool, force: bool) -> int:
    """Основной проход. Возвращает количество реально залитых источников."""
    items = collect_docset(mode, geo_filter)
    if not items:
        print("doc-set пуст — нечего синхронизировать")
        return 0

    client = NotebookLMClient()
    if not await client.is_authenticated():
        raise NotebookLMError(
            "notebooklm не авторизован — выполни: notebooklm login --browser-cookies chrome"
        )

    manifest = KbManifest.load(MANIFEST_PATH)
    notebooks = {nb.title: nb.id for nb in await client.list_notebooks()}
    sources_cache: dict[str, set[str]] = {}
    now = datetime.now(timezone.utc).isoformat()

    # Группируем по целевому ноутбуку для аккуратного вывода
    by_target: dict[str, list[DocItem]] = {}
    for item in items:
        by_target.setdefault(item.target, []).append(item)

    added = 0
    for target, group in by_target.items():
        title = _resolve_title(target)
        nb_id = notebooks.get(title)
        marker = "есть" if nb_id else "создать"
        print(f"\n▸ Ноутбук «{title}» ({marker}) — {len(group)} файлов")

        if nb_id is None and not dry_run:
            nb_id = await client.create_notebook(title)
            notebooks[title] = nb_id
        # Существующие источники ноутбука (вторая защита от дублей при потере манифеста)
        existing = sources_cache.get(nb_id or "", set())
        if nb_id and nb_id not in sources_cache:
            existing = {s.title for s in await client.list_sources(nb_id)}
            sources_cache[nb_id] = existing

        for item in group:
            sha = await asyncio.to_thread(sha256_of_file, item.abs_path)
            key_nb = nb_id or f"DRY:{title}"
            if not force and manifest.is_synced(key_nb, item.rel_path, sha):
                print(f"  [skip] не изменился: {item.rel_path}")
                continue
            if not force and item.rel_path in existing:
                print(f"  [skip] уже в ноутбуке (manifest stale): {item.rel_path}")
                manifest.mark(
                    notebook_id=key_nb, path=item.rel_path, sha256=sha, title=title, synced_at=now
                )
                continue
            if dry_run:
                print(f"  [ADD] {item.rel_path}")
                continue
            source = await prepare_source(item)
            if source is None:
                continue
            await client.add_source(nb_id, source, title=item.rel_path)
            manifest.mark(
                notebook_id=key_nb, path=item.rel_path, sha256=sha, title=title, synced_at=now
            )
            existing.add(item.rel_path)
            added += 1
            print(f"  [ADD] {item.rel_path}")

    if not dry_run:
        manifest.save(MANIFEST_PATH)
        print(f"\n✅ Залито источников: {added}. Манифест: {_rel(MANIFEST_PATH)}")
    else:
        print("\n[dry-run] заливки не было.")
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="KB Sync: docs → NotebookLM (идемпотентно)")
    parser.add_argument(
        "--notebook-mode",
        choices=("shared", "per-geo"),
        default="per-geo",
        help="shared = один ноутбук; per-geo = методология + ноутбук на гео (дефолт)",
    )
    parser.add_argument("--geo", default=None, help="ограничить гео-часть одним кодом (напр. GH)")
    parser.add_argument("--dry-run", action="store_true", help="показать план без заливки")
    parser.add_argument("--force", action="store_true", help="игнорировать манифест (риск дублей)")
    args = parser.parse_args()
    asyncio.run(
        sync(
            mode=args.notebook_mode,
            geo_filter=args.geo,
            dry_run=args.dry_run,
            force=args.force,
        )
    )


if __name__ == "__main__":
    main()
