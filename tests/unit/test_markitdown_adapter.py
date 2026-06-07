# -*- coding: utf-8 -*-
"""Тесты markitdown-адаптера: мок subprocess + резолв бинаря + реальный smoke."""

from __future__ import annotations

import asyncio
import shutil

import pytest

from core.markitdown_adapter import MarkitdownError, resolve_markitdown, to_markdown


class _FakeProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._o = stdout
        self._e = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._o, self._e

    def kill(self) -> None: ...

    async def wait(self) -> int:
        return self.returncode


def _patch(monkeypatch, proc: _FakeProc) -> None:
    async def fake(*a, **k):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)


# stdout markitdown атомарно пишется в dst (с созданием родительских папок)
async def test_to_markdown_writes_stdout(monkeypatch, tmp_path):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF")
    _patch(monkeypatch, _FakeProc(stdout="# Заголовок".encode()))
    dst = tmp_path / "out" / "a.md"
    res = await to_markdown(src, dst, binary="/fake/markitdown")
    assert res == dst and dst.read_text("utf-8") == "# Заголовок"


# несуществующий src → ошибка до запуска
async def test_missing_src(tmp_path):
    with pytest.raises(MarkitdownError):
        await to_markdown(tmp_path / "нет.pdf", tmp_path / "o.md", binary="/fake/markitdown")


# ненулевой код → ошибка с хвостом stderr
async def test_nonzero_exit(monkeypatch, tmp_path):
    src = tmp_path / "a.docx"
    src.write_bytes(b"x")
    _patch(monkeypatch, _FakeProc(stderr=b"bad", returncode=1))
    with pytest.raises(MarkitdownError) as ei:
        await to_markdown(src, tmp_path / "o.md", binary="/fake/markitdown")
    assert "bad" in str(ei.value)


# бинарь не найден → подсказка по установке
async def test_filenotfound(monkeypatch, tmp_path):
    src = tmp_path / "a.csv"
    src.write_text("a,b")

    async def fake(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    with pytest.raises(MarkitdownError):
        await to_markdown(src, tmp_path / "o.md", binary="/fake/markitdown")


# resolve с explicit возвращает его как есть
def test_resolve_explicit():
    assert resolve_markitdown("/x/md") == "/x/md"


# реальный smoke: csv → markdown (если markitdown установлен)
@pytest.mark.skipif(shutil.which("markitdown") is None, reason="markitdown не установлен")
async def test_real_csv_conversion(tmp_path):
    src = tmp_path / "t.csv"
    src.write_text("name,val\nA,1\nB,2\n")
    dst = tmp_path / "t.md"
    await to_markdown(src, dst)
    text = dst.read_text("utf-8")
    assert "A" in text and "B" in text
