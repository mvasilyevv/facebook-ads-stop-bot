# -*- coding: utf-8 -*-
"""Read-only summary of canonical pause/activate command lifecycles."""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import text

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError


class GetAdActionStatusTool:
    """Group canonical Meta ad actions by action and durable status."""

    name: ClassVar[str] = "get_ad_action_status"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_ad_action_status",
        "description": (
            "Сводка pause/activate действий Meta API по status за последние N часов. "
            "Полезно для диагностики очереди и залипших money-actions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "minimum": 1, "maximum": 168, "default": 24},
                "action": {
                    "type": "string",
                    "enum": ["pause", "activate", "both"],
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

        action = str(args.get("action") or "both").strip().lower()
        if action not in {"pause", "activate", "both"}:
            raise ToolError(f"action должен быть pause/activate/both, получено: {action!r}")

        params: dict[str, Any] = {"hrs": hours}
        action_filter = ""
        if action != "both":
            action_filter = "AND payload->>'mutation_kind' = :mutation_kind"
            params["mutation_kind"] = "pause_ad" if action == "pause" else "activate_ad"

        rows = []
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        f"""
                        SELECT
                            CASE payload->>'mutation_kind'
                                WHEN 'pause_ad' THEN 'pause'
                                WHEN 'activate_ad' THEN 'activate'
                            END AS action,
                            status,
                            COUNT(*) AS cnt
                        FROM task_queue
                        WHERE task_type = 'meta_api_mutation'
                          AND payload->>'mutation_kind' IN ('pause_ad', 'activate_ad')
                          {action_filter}
                          AND created_at >= NOW() - make_interval(hours => :hrs)
                        GROUP BY action, status
                        ORDER BY action, status
                        """
                    ),
                    params,
                )
            ).all()

        if not rows:
            return f"Pause/activate действий за последние {hours}ч нет."

        lines = [f"Pause/activate действия за последние {hours}ч:"]
        lines.extend(f"- {row.action}/{row.status}: {row.cnt}" for row in rows)
        return "\n".join(lines)
