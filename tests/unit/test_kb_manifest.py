# -*- coding: utf-8 -*-
"""Тесты манифеста KB: sha256-дельта, идемпотентность, round-trip, битый файл."""

from __future__ import annotations

import pytest

from core.notebooklm.kb_manifest import KbManifest, KbManifestError, sha256_of_file


# sha256_of_file меняется при правке содержимого и имеет длину 64
def test_sha256_changes_on_edit(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    h1 = sha256_of_file(f)
    f.write_text("hello!")
    h2 = sha256_of_file(f)
    assert h1 != h2 and len(h1) == 64


# is_synced True только при совпадении тройки notebook_id+path+sha
def test_is_synced_matches_triple(tmp_path):
    m = KbManifest(manifest_path=tmp_path / "m.json")
    m.mark(notebook_id="nb", path="docs/a.md", sha256="aa", title="T", synced_at="t")
    assert m.is_synced("nb", "docs/a.md", "aa")
    assert not m.is_synced("nb", "docs/a.md", "bb")  # sha изменился
    assert not m.is_synced("other", "docs/a.md", "aa")  # другой ноутбук


# save→load сохраняет записи
def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "m.json"
    m = KbManifest(manifest_path=p)
    m.mark(notebook_id="nb", path="x", sha256="s", title="T", synced_at="t")
    m.save()
    assert KbManifest.load(p).is_synced("nb", "x", "s")


# идемпотентность: после mark+save повторная загрузка видит файл как уже synced (0 новых)
def test_idempotent_second_pass(tmp_path):
    p = tmp_path / "m.json"
    src = tmp_path / "doc.md"
    src.write_text("data")
    sha = sha256_of_file(src)
    first = KbManifest.load(p)
    assert not first.is_synced("nb", "doc.md", sha)
    first.mark(notebook_id="nb", path="doc.md", sha256=sha, title="T", synced_at="t")
    first.save()
    assert KbManifest.load(p).is_synced("nb", "doc.md", sha)


# повторный mark той же пары не плодит дублей (одна запись на ключ)
def test_mark_overwrites_same_key(tmp_path):
    m = KbManifest(manifest_path=tmp_path / "m.json")
    m.mark(notebook_id="nb", path="x", sha256="s1", title="T", synced_at="t")
    m.mark(notebook_id="nb", path="x", sha256="s2", title="T", synced_at="t")
    entries = m.entries()
    assert len(entries) == 1 and entries[0].sha256 == "s2"


# битый JSON → KbManifestError
def test_corrupt_manifest_raises(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{bad")
    with pytest.raises(KbManifestError):
        KbManifest.load(p)


# отсутствующий файл → пустой манифест
def test_missing_file_empty(tmp_path):
    assert KbManifest.load(tmp_path / "nope.json").entries() == []


# save без пути назначения → ошибка
def test_save_without_path_raises():
    with pytest.raises(KbManifestError):
        KbManifest().save()
