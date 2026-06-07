# -*- coding: utf-8 -*-
"""Async-обёртка над CLI `notebooklm` (notebooklm-py) через subprocess.

CLI живёт в venv 3.12 (`~/.notebooklm-venv`, симлинк `~/.local/bin/notebooklm`), а
проектный рантайм — 3.14, поэтому НЕ импортируем пакет, а шеллим в бинарь — та же
граница процесса, что и `video_uniquifier._run_tool` (ffmpeg) и `folder_opener` (open).
Преимущество: ни одной новой зависимости в pyproject; неофициальный CLI скрейпит
NotebookLM и при смене UI Google ломается — чиним только этот файл.

Все запросы используют `--json`, где он есть (list/create/source list/ask) — парсим
структурированный вывод, а не rich-таблицы (главный источник хрупкости).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_INSTALL_HINT = (
    "notebooklm CLI не найден — ожидается ~/.local/bin/notebooklm (venv 3.12). "
    "Установка: pip install 'notebooklm-py[browser,cookies]' в отдельный venv + симлинк."
)

# Таймауты сабкоманд (сек). Не выносим в публичные параметры методов — это сетевые
# вызовы CLI, переопределение вызывающим не нужно (и триггерит ruff ASYNC109).
_DEFAULT_TIMEOUT_S = 60.0
_ADD_SOURCE_TIMEOUT_S = 240.0
_ASK_TIMEOUT_S = 120.0
_DOCTOR_TIMEOUT_S = 30.0


class NotebookLMError(RuntimeError):
    """Ошибка вызова notebooklm CLI или разбора его вывода."""


@dataclass(frozen=True)
class Notebook:
    """Ноутбук NotebookLM (из `list --json`)."""

    id: str
    title: str
    is_owner: bool = False
    created_at: str | None = None


@dataclass(frozen=True)
class Source:
    """Источник внутри ноутбука (из `source list --json`)."""

    id: str
    title: str
    status: str | None = None


def resolve_notebooklm(explicit: str | None = None) -> str:
    """Возвращает путь к бинарю notebooklm: явный → ~/.local/bin → PATH."""
    if explicit:
        return explicit
    candidate = Path.home() / ".local" / "bin" / "notebooklm"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("notebooklm")
    if not found:
        raise NotebookLMError(_INSTALL_HINT)
    return found


def _extract_id(data: Any) -> str | None:
    """Толерантно достаёт id ноутбука из разных форм вывода `create --json`."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("id"), str):
        return data["id"]
    nb = data.get("notebook")
    if isinstance(nb, dict) and isinstance(nb.get("id"), str):
        return nb["id"]
    nbs = data.get("notebooks")
    if isinstance(nbs, list) and nbs and isinstance(nbs[0], dict):
        got = nbs[0].get("id")
        return got if isinstance(got, str) else None
    return None


class NotebookLMClient:
    """Тонкий клиент: один метод = одна сабкоманда CLI."""

    def __init__(self, *, binary: str | None = None) -> None:
        # explicit binary не проверяем на существование — это упрощает мок в тестах.
        self._binary = binary or resolve_notebooklm()

    async def _run_cli(
        self,
        args: list[str],
        *,
        timeout_s: float,
        allow_nonzero: bool = False,
    ) -> str:
        """Запускает CLI и возвращает stdout. Маппит ошибки в NotebookLMError."""
        try:
            process = await asyncio.create_subprocess_exec(
                self._binary,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise NotebookLMError(_INSTALL_HINT) from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            cmd = args[0] if args else ""
            raise NotebookLMError(f"notebooklm {cmd} превысил таймаут {timeout_s:.0f}s") from exc

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if process.returncode != 0 and not allow_nonzero:
            tail = stderr.strip()[-600:]
            cmd = " ".join(args[:2])
            raise NotebookLMError(f"notebooklm {cmd} ошибка (код {process.returncode}): {tail}")
        if allow_nonzero:
            # doctor печатает таблицу в stdout даже при ненулевом коде (Auth fail).
            return stdout + stderr
        return stdout

    def _loads(self, raw: str, *, context: str) -> Any:
        """JSON-парсинг вывода CLI с понятной ошибкой."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NotebookLMError(
                f"неожиданный вывод notebooklm ({context}): {raw[:200]!r}"
            ) from exc

    async def list_notebooks(self) -> list[Notebook]:
        """`notebooklm list --json` → список ноутбуков."""
        raw = await self._run_cli(["list", "--json"], timeout_s=_DEFAULT_TIMEOUT_S)
        data = self._loads(raw, context="list")
        items = data.get("notebooks", []) if isinstance(data, dict) else []
        out: list[Notebook] = []
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("id"), str):
                continue
            out.append(
                Notebook(
                    id=it["id"],
                    title=str(it.get("title") or ""),
                    is_owner=bool(it.get("is_owner", False)),
                    created_at=it.get("created_at"),
                )
            )
        return out

    async def create_notebook(self, title: str) -> str:
        """`notebooklm create TITLE --json` → id нового ноутбука."""
        if not title.strip():
            raise NotebookLMError("title ноутбука не может быть пустым")
        raw = await self._run_cli(["create", title, "--json"], timeout_s=_DEFAULT_TIMEOUT_S)
        nb_id = _extract_id(self._loads(raw, context="create"))
        if not nb_id:
            raise NotebookLMError(f"не удалось получить id из вывода create: {raw[:200]!r}")
        return nb_id

    async def list_sources(self, notebook_id: str) -> list[Source]:
        """`notebooklm source list -n <id> --json` → источники ноутбука."""
        raw = await self._run_cli(
            ["source", "list", "-n", notebook_id, "--json"], timeout_s=_DEFAULT_TIMEOUT_S
        )
        data = self._loads(raw, context="source list")
        if isinstance(data, dict):
            items = data.get("sources", [])
        elif isinstance(data, list):
            items = data
        else:
            items = []
        out: list[Source] = []
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("id"), str):
                continue
            out.append(
                Source(
                    id=it["id"],
                    title=str(it.get("title") or ""),
                    status=it.get("status"),
                )
            )
        return out

    async def add_source(
        self,
        notebook_id: str,
        path: Path,
        *,
        title: str | None = None,
        source_type: str = "file",
    ) -> None:
        """`notebooklm source add <path> -n <id> --type file [--title ...]`."""
        args = ["source", "add", str(path), "-n", notebook_id, "--type", source_type]
        if title:
            args += ["--title", title]
        await self._run_cli(args, timeout_s=_ADD_SOURCE_TIMEOUT_S)

    async def ask(self, notebook_id: str, question: str) -> str:
        """`notebooklm ask QUESTION -n <id>` → текст ответа (для смоук-Q&A)."""
        raw = await self._run_cli(["ask", question, "-n", notebook_id], timeout_s=_ASK_TIMEOUT_S)
        return raw.strip()

    async def doctor(self) -> str:
        """`notebooklm doctor` (raw текст; код != 0 при Auth fail допустим)."""
        return await self._run_cli(["doctor"], timeout_s=_DOCTOR_TIMEOUT_S, allow_nonzero=True)

    async def is_authenticated(self) -> bool:
        """True, если doctor не сообщает «not authenticated»."""
        text = await self.doctor()
        return "not authenticated" not in text.lower()
