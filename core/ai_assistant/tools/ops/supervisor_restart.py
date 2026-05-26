# -*- coding: utf-8 -*-
"""Tool supervisor_restart — перезапуск worker через supervisorctl."""

from __future__ import annotations

from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolError

# Whitelist допустимых процессов для перезапуска
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


class SupervisorRestartTool:
    """Перезапуск worker через supervisor.

    Применяется когда лог завис, воркер крашится, или нужно переподключить браузер.
    """

    name: ClassVar[str] = "supervisor_restart"
    risk_level: ClassVar[RiskLevel] = RiskLevel.DRAFT_REQUIRED
    schema: ClassVar[dict[str, Any]] = {
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
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Перезапустить процесс через supervisorctl (whitelist-protected)."""
        process = str(args.get("process", ""))
        if process not in ALLOWED_SUPERVISOR_PROCESSES:
            raise ToolError(f"процесс '{process}' не в whitelist")
        from apps.health_watchdog.main import restart_via_supervisor

        await restart_via_supervisor(process)
        return f"OK: процесс {process} перезапущен через supervisor"
