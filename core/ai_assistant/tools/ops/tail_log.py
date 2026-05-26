# -*- coding: utf-8 -*-
"""Tool tail_log — чтение последних строк лог-файла."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolError

# Whitelist допустимых лог-файлов
ALLOWED_LOG_FILES: frozenset[str] = frozenset(
    {
        "observer.log",
        "telegram.log",
        "supervisord.log",
        "browser_agent.log",
        "disable_worker.log",
        "enable_worker.log",
        "enable_recommendation_worker.log",
        "health_watchdog.log",
        "api.log",
    }
)

# Корень репозитория — на 4 уровня выше этого файла (core/ai_assistant/tools/ops/tail_log.py)
_REPO_ROOT = Path(__file__).resolve().parents[4]
_LOGS_DIR = _REPO_ROOT / ".logs"


class TailLogTool:
    """Чтение последних строк лог-файла для диагностики ошибок."""

    name: ClassVar[str] = "tail_log"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "tail_log",
        "description": (
            "Прочитать последние строки лог-файла. Используй для диагностики ошибок "
            "и поиска причины алертов."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "log_name": {
                    "type": "string",
                    "enum": sorted(ALLOWED_LOG_FILES),
                    "description": "Имя файла в .logs/",
                },
                "lines": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Количество строк с конца (max 200)",
                },
            },
            "required": ["log_name"],
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Читает последние N строк из .logs/<log_name>."""
        log_name = str(args.get("log_name", ""))
        lines = int(args.get("lines") or 50)
        if log_name not in ALLOWED_LOG_FILES:
            raise ToolError(f"лог '{log_name}' не в whitelist")
        lines = max(1, min(lines, 200))
        log_path = _LOGS_DIR / log_name

        def _read() -> str:
            if not log_path.exists():
                return f"(лог {log_name} не существует)"
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-lines:]
            return "".join(tail)

        return await asyncio.to_thread(_read)
