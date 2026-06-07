# -*- coding: utf-8 -*-
"""Конвертация документов в Markdown через CLI `markitdown` (PDF/DOCX/PPTX/XLSX/CSV/HTML).

markitdown живёт в отдельном venv 3.12 (`~/.markitdown-venv`), а проектный рантайм —
3.14, поэтому не импортируем пакет, а шеллим в бинарь через subprocess — та же
граница процесса, что и в `core/creatives/video_uniquifier._run_tool` (ffmpeg).
Это держит зависимость за пределами `pyproject.toml` и чинится правкой одного файла.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

# Форматы, которые markitdown[all] умеет конвертировать (для понятной ранней ошибки).
SUPPORTED_SUFFIXES = frozenset(
    {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".tsv", ".html", ".htm"}
)

_INSTALL_HINT = (
    "markitdown не найден — установите: "
    "python3.12 -m venv ~/.markitdown-venv && "
    "~/.markitdown-venv/bin/pip install 'markitdown[all]' && "
    "ln -s ~/.markitdown-venv/bin/markitdown ~/.local/bin/markitdown"
)


class MarkitdownError(RuntimeError):
    """Ошибка конвертации документа в Markdown."""


def resolve_markitdown(explicit: str | None = None) -> str:
    """Возвращает путь к бинарю markitdown: явный → ~/.local/bin → PATH."""
    if explicit:
        return explicit
    candidate = Path.home() / ".local" / "bin" / "markitdown"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("markitdown")
    if not found:
        raise MarkitdownError(_INSTALL_HINT)
    return found


async def to_markdown(
    src: Path,
    dst: Path,
    *,
    binary: str | None = None,
    timeout_s: float = 120.0,
) -> Path:
    """Конвертирует `src` в Markdown и атомарно пишет в `dst`. Возвращает `dst`.

    stdout markitdown перехватывается и пишется сами (temp+rename), чтобы не зависеть
    от синтаксиса флага `-o` и получить атомарную запись.
    """
    src = await asyncio.to_thread(src.expanduser)
    if not await asyncio.to_thread(src.is_file):
        raise MarkitdownError(f"Файл «{src}» не найден")

    bin_path = resolve_markitdown(binary)
    try:
        process = await asyncio.create_subprocess_exec(
            bin_path,
            str(src),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise MarkitdownError(_INSTALL_HINT) from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise MarkitdownError(
            f"markitdown превысил таймаут {timeout_s:.0f}s на «{src.name}»"
        ) from exc

    if process.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise MarkitdownError(f"markitdown завершился с ошибкой на «{src.name}»: {tail}")

    text = stdout.decode("utf-8", errors="replace")
    await asyncio.to_thread(dst.parent.mkdir, parents=True, exist_ok=True)
    tmp = dst.parent / f".tmp_{dst.name}_{uuid4().hex}"
    await asyncio.to_thread(tmp.write_text, text, "utf-8")
    await asyncio.to_thread(tmp.replace, dst)
    return dst
