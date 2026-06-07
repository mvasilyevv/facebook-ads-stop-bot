# -*- coding: utf-8 -*-
"""Тесты адаптера NotebookLM CLI — мок subprocess, без реального бинаря и сети."""

from __future__ import annotations

import asyncio
import json

import pytest

from core.notebooklm.client import NotebookLMClient, NotebookLMError, _extract_id


class _FakeProc:
    """Поддельный subprocess: отдаёт заданные stdout/stderr/код, умеет «висеть»."""

    def __init__(
        self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0, hang: bool = False
    ):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def _patch(monkeypatch, proc: _FakeProc, capture: list | None = None) -> None:
    async def fake(*args, **kwargs):
        if capture is not None:
            capture.extend(args)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)


def _client() -> NotebookLMClient:
    return NotebookLMClient(binary="/fake/notebooklm")


# list --json парсится в Notebook со всеми полями + argv собран верно
async def test_list_notebooks_parses_json(monkeypatch):
    payload = json.dumps(
        {
            "notebooks": [{"id": "abc", "title": "T", "is_owner": True, "created_at": "2026"}],
            "count": 1,
        }
    ).encode()
    cap: list = []
    _patch(monkeypatch, _FakeProc(stdout=payload), cap)
    nbs = await _client().list_notebooks()
    assert len(nbs) == 1 and nbs[0].id == "abc" and nbs[0].title == "T" and nbs[0].is_owner
    assert "list" in cap and "--json" in cap


# элементы без id отбрасываются (толерантность к мусору)
async def test_list_skips_items_without_id(monkeypatch):
    payload = json.dumps({"notebooks": [{"title": "no id"}, {"id": "ok", "title": "y"}]}).encode()
    _patch(monkeypatch, _FakeProc(stdout=payload))
    nbs = await _client().list_notebooks()
    assert [n.id for n in nbs] == ["ok"]


# source list --json → Source с id/title
async def test_list_sources_parses(monkeypatch):
    payload = json.dumps(
        {"sources": [{"id": "s1", "title": "docs/a.md", "status": "ready"}]}
    ).encode()
    _patch(monkeypatch, _FakeProc(stdout=payload))
    srcs = await _client().list_sources("nb1")
    assert srcs[0].id == "s1" and srcs[0].title == "docs/a.md"


# create --json извлекает id из разных форм вывода
@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"id": "n1"}, "n1"),
        ({"notebook": {"id": "n2"}}, "n2"),
        ({"notebooks": [{"id": "n3"}]}, "n3"),
    ],
)
async def test_create_extracts_id(monkeypatch, payload, expected):
    _patch(monkeypatch, _FakeProc(stdout=json.dumps(payload).encode()))
    assert await _client().create_notebook("Title") == expected


# create без id в выводе → ошибка
async def test_create_missing_id_raises(monkeypatch):
    _patch(monkeypatch, _FakeProc(stdout=b'{"x":1}'))
    with pytest.raises(NotebookLMError):
        await _client().create_notebook("T")


# пустой title отвергается до вызова CLI
async def test_create_empty_title(monkeypatch):
    with pytest.raises(NotebookLMError):
        await _client().create_notebook("  ")


# add_source собирает argv с -n/--type/--title
async def test_add_source_argv(monkeypatch, tmp_path):
    cap: list = []
    _patch(monkeypatch, _FakeProc(), cap)
    path = tmp_path / "f.md"
    path.write_text("x")
    await _client().add_source("nb1", path, title="docs/f.md")
    assert "source" in cap and "add" in cap and "nb1" in cap and "docs/f.md" in cap


# ненулевой код CLI → NotebookLMError с хвостом stderr
async def test_nonzero_exit_raises(monkeypatch):
    _patch(monkeypatch, _FakeProc(stderr=b"boom", returncode=2))
    with pytest.raises(NotebookLMError) as ei:
        await _client().list_notebooks()
    assert "boom" in str(ei.value)


# бинарь не найден → NotebookLMError
async def test_filenotfound_raises(monkeypatch):
    async def fake(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    with pytest.raises(NotebookLMError):
        await _client().list_notebooks()


# таймаут убивает процесс и бросает ошибку
async def test_timeout_kills(monkeypatch):
    proc = _FakeProc(hang=True)
    _patch(monkeypatch, proc)
    with pytest.raises(NotebookLMError):
        await _client()._run_cli(["list"], timeout_s=0.01)
    assert proc.killed


# битый JSON → понятная ошибка с контекстом
async def test_bad_json_raises(monkeypatch):
    _patch(monkeypatch, _FakeProc(stdout=b"not json"))
    with pytest.raises(NotebookLMError):
        await _client().list_notebooks()


# doctor с «not authenticated» (код != 0) → is_authenticated False, без исключения
async def test_is_authenticated_false(monkeypatch):
    _patch(monkeypatch, _FakeProc(stdout=b"Auth: not authenticated", returncode=1))
    assert await _client().is_authenticated() is False


# doctor «All checks passed» → is_authenticated True
async def test_is_authenticated_true(monkeypatch):
    _patch(monkeypatch, _FakeProc(stdout=b"All checks passed", returncode=0))
    assert await _client().is_authenticated() is True


# _extract_id возвращает None на пустых/неподходящих формах
def test_extract_id_none_cases():
    assert _extract_id({}) is None
    assert _extract_id("x") is None
    assert _extract_id({"notebooks": []}) is None
