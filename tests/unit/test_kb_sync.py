# -*- coding: utf-8 -*-
"""Тесты сборки doc-set и подготовки источников kb_sync (без сети и CLI).

kb_sync.py — скрипт (не пакет), грузим по пути через importlib.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_kb_sync():
    spec = importlib.util.spec_from_file_location("kb_sync_mod", REPO / "scripts" / "kb_sync.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # без регистрации dataclass на 3.14 не резолвит аннотации
    spec.loader.exec_module(mod)
    return mod


# per-geo: методология в shared, гео-файлы — в собственные ноутбуки
def test_collect_docset_per_geo():
    mod = _load_kb_sync()
    items = mod.collect_docset("per-geo", None)
    rels = {i.rel_path: i.target for i in items}
    assert rels.get("docs/creative_kb.md") == mod.SHARED
    geo_items = [i for i in items if i.rel_path.endswith("geo.yaml")]
    assert geo_items and all(i.target != mod.SHARED for i in geo_items)


# shared: гео-файлы сваливаются в общий ноутбук
def test_collect_docset_shared():
    mod = _load_kb_sync()
    items = mod.collect_docset("shared", None)
    geo_items = [i for i in items if i.rel_path.endswith("geo.yaml")]
    assert geo_items and all(i.target == mod.SHARED for i in geo_items)


# фильтр по гео оставляет в гео-части только указанный код
def test_collect_docset_geo_filter():
    mod = _load_kb_sync()
    items = mod.collect_docset("per-geo", "GH")
    geo_targets = {i.target for i in items if i.target != mod.SHARED}
    assert geo_targets == {"GH"}


# playbooks попадают в doc-set (методология)
def test_collect_docset_includes_playbooks():
    mod = _load_kb_sync()
    items = mod.collect_docset("per-geo", None)
    assert any(i.rel_path.startswith("docs/playbooks/") for i in items)


# prepare_source: yaml оборачивается в md code-fence в KB_BUILD
async def test_prepare_source_yaml_fence(tmp_path, monkeypatch):
    mod = _load_kb_sync()
    monkeypatch.setattr(mod, "KB_BUILD", tmp_path / "build")
    src = tmp_path / "x.yaml"
    src.write_text("a: 1\n")
    item = mod.DocItem(abs_path=src, rel_path="docs/x.yaml", target=mod.SHARED)
    out = await mod.prepare_source(item)
    text = out.read_text("utf-8")
    assert "```yaml" in text and "a: 1" in text


# prepare_source: .md возвращается как есть (без копии)
async def test_prepare_source_md_passthrough(tmp_path):
    mod = _load_kb_sync()
    src = tmp_path / "x.md"
    src.write_text("# hi")
    item = mod.DocItem(abs_path=src, rel_path="docs/x.md", target=mod.SHARED)
    assert await mod.prepare_source(item) == src
