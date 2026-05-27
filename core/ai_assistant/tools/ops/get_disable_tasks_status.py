# -*- coding: utf-8 -*-
"""Tool get_disable_tasks_status — сводка по очереди disable/enable задач."""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import text

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError


class GetDisableTasksStatusTool:
    """Группировка task_queue по task_type='disable'/'enable' и status."""

    name: ClassVar[str] = "get_disable_tasks_status"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_disable_tasks_status",
        "description": (
            "Сводка по очереди disable/enable задач (count по status за последние N часов). "
            "Полезно для диагностики залипших задач и быстрого взгляда на pipeline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "minimum": 1, "maximum": 168, "default": 24},
                "task_type": {
                    "type": "string",
                    "enum": ["disable", "enable", "both"],
                    "default": "both",
                },
            },
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        engine = ctx.require_engine()
        try:
            hours = int(args.get("hours") or 24)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"hours должен быть целым: {exc}") from exc
        hours = max(1, min(hours, 168))

        task_type = str(args.get("task_type") or "both").strip().lower()
        if task_type not in ("disable", "enable", "both"):
            raise ToolError(f"task_type должен быть disable/enable/both, получено: {task_type!r}")

        params: dict[str, Any] = {"hrs": hours}
        if task_type == "both":
            tt_filter = "task_type IN ('disable', 'enable')"
        else:
            tt_filter = "task_type = :tt"
            params["tt"] = task_type

        sql = (
            "SELECT task_type, status, COUNT(*) AS cnt "
            "FROM task_queue "
            f"WHERE {tt_filter} "
            "  AND created_at >= NOW() - make_interval(hours => :hrs) "
            "GROUP BY task_type, status "
            "ORDER BY task_type, status"
        )
        async with engine.connect() as conn:
            rows = (await conn.execute(text(sql), params)).all()

        if not rows:
            return f"Задач disable/enable за последние {hours}ч нет."

        lines = [f"Задачи disable/enable за последние {hours}ч:"]
        for row in rows:
            tt, st, cnt = row
            lines.append(f"- {tt}/{st}: {cnt}")
        return "\n".join(lines)
