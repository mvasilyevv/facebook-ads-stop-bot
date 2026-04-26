# -*- coding: utf-8 -*-
"""Безопасное открытие папок с готовыми креативами."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from core.creatives.service import default_creatives_root


class CreativeFolderOpenError(ValueError):
    """Ошибка открытия папки с результатом."""


async def open_generated_folder(path: str, *, base_dir: Path | None = None) -> None:
    """Открывает только существующие папки внутри корня FB_Agent_Creo."""
    root = await asyncio.to_thread(lambda: (base_dir or default_creatives_root()).resolve())
    target = await asyncio.to_thread(lambda: Path(path).expanduser().resolve())
    target_exists = await asyncio.to_thread(target.exists)
    target_is_dir = await asyncio.to_thread(target.is_dir)

    if not target_exists or not target_is_dir:
        raise CreativeFolderOpenError("Папка с результатом не найдена")
    if not target.is_relative_to(root):
        raise CreativeFolderOpenError("Можно открывать только папки внутри FB_Agent_Creo")

    if sys.platform == "darwin":
        command = ["open", str(target)]
    else:
        opener = shutil.which("xdg-open")
        if not opener:
            raise CreativeFolderOpenError(
                "Открытие папки поддерживается только на локальной машине"
            )
        command = [opener, str(target)]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.communicate()
    if process.returncode != 0:
        raise CreativeFolderOpenError("Не удалось открыть папку с результатом")
