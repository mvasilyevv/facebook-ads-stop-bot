# -*- coding: utf-8 -*-
"""Whitelisted tools для AI-помощника.

LLM может вызывать только эти 4 инструмента, и только с белосписочными
аргументами. Никаких произвольных shell-команд или путей.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from core.config import get_settings

logger = logging.getLogger(__name__)


# --- Whitelist ---

ALLOWED_SUPERVISOR_PROCESSES: frozenset[str] = frozenset(
    {
        "observer_worker",
        "telegram_poller",
        "disable_worker",
        "enable_worker",
        "enable_recommendation_worker",
        "browser_agent",
    }
)

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

# Корень репозитория — на 3 уровня выше этого файла (core/ai_assistant/tools.py)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOGS_DIR = _REPO_ROOT / ".logs"


# --- JSON schema для Anthropic tools ---

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "supervisor_restart",
        "description": (
            "Перезапустить worker через supervisor. Допустимы только воркеры из "
            "whitelist. Используй когда лог завис, воркер крашится, или нужно "
            "переподключить браузер."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "process": {
                    "type": "string",
                    "enum": sorted(ALLOWED_SUPERVISOR_PROCESSES),
                    "description": "Имя процесса в supervisord",
                },
            },
            "required": ["process"],
        },
    },
    {
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
    },
    {
        "name": "api_get",
        "description": (
            "GET-запрос к внутреннему API на 127.0.0.1:8100. Только GET, путь должен "
            "начинаться на /api/. Используй для проверки состояния воркеров, очередей, настроек."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Путь, например /api/dashboard/stats",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "set_scanning",
        "description": (
            "Включить/выключить сканирование observer'а. Используй только если "
            "пользователь явно просит — это влияет на всю работу бота."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "true — включить, false — выключить",
                },
            },
            "required": ["enabled"],
        },
    },
]


class ToolError(Exception):
    """Ошибка выполнения tool (нарушен whitelist либо runtime-ошибка)."""


# --- Реализация ---


async def _run_supervisor_restart(process: str) -> str:
    """Перезапускает worker через supervisor (whitelist + переиспользуем helper)."""
    if process not in ALLOWED_SUPERVISOR_PROCESSES:
        raise ToolError(f"процесс '{process}' не в whitelist")
    from apps.health_watchdog.main import restart_via_supervisor

    await restart_via_supervisor(process)
    return f"OK: процесс {process} перезапущен через supervisor"


async def _run_tail_log(log_name: str, lines: int = 50) -> str:
    """Читает последние N строк из .logs/<log_name>."""
    if log_name not in ALLOWED_LOG_FILES:
        raise ToolError(f"лог '{log_name}' не в whitelist")
    lines = max(1, min(int(lines), 200))
    log_path = _LOGS_DIR / log_name

    def _read() -> str:
        if not log_path.exists():
            return f"(лог {log_name} не существует)"
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-lines:]
        return "".join(tail)

    return await asyncio.to_thread(_read)


async def _run_api_get(path: str) -> str:
    """GET к внутреннему API на 127.0.0.1:8100."""
    if not path.startswith("/api/"):
        raise ToolError("path должен начинаться на /api/")
    if "://" in path or ".." in path:
        raise ToolError("неправильный path")

    settings = get_settings()
    url = f"http://127.0.0.1:{settings.api_port}{path}"
    headers: dict[str, str] = {}
    if settings.api_key:
        headers["X-API-Key"] = settings.api_key

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ToolError(f"запрос {path} не удался: {exc}") from exc

    text = resp.text
    if len(text) > 4000:
        text = text[:4000] + "\n... (обрезано)"
    return f"HTTP {resp.status_code}\n{text}"


async def _run_set_scanning(enabled: bool) -> str:
    """PATCH /api/settings/observer/scanning."""
    settings = get_settings()
    url = f"http://127.0.0.1:{settings.api_port}/api/settings/observer/scanning"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["X-API-Key"] = settings.api_key

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                url, headers=headers, json={"is_scanning_enabled": bool(enabled)}
            )
    except httpx.HTTPError as exc:
        raise ToolError(f"set_scanning запрос не удался: {exc}") from exc

    if resp.status_code >= 400:
        raise ToolError(f"set_scanning HTTP {resp.status_code}: {resp.text[:200]}")
    return f"OK: сканирование {'включено' if enabled else 'выключено'}"


_DISPATCH = {
    "supervisor_restart": lambda args: _run_supervisor_restart(str(args.get("process", ""))),
    "tail_log": lambda args: _run_tail_log(
        str(args.get("log_name", "")), int(args.get("lines") or 50)
    ),
    "api_get": lambda args: _run_api_get(str(args.get("path", ""))),
    "set_scanning": lambda args: _run_set_scanning(bool(args.get("enabled", False))),
}


async def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Выполнить tool по имени. Логирует попытку и результат."""
    if name not in _DISPATCH:
        raise ToolError(f"неизвестный tool: {name}")
    logger.info("AI tool invocation: %s args=%s", name, args)
    try:
        result = await _DISPATCH[name](args)
        logger.info("AI tool %s OK", name)
        return result
    except ToolError:
        raise
    except Exception as exc:
        logger.exception("AI tool %s ошибка", name)
        raise ToolError(str(exc)) from exc
