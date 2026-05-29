# -*- coding: utf-8 -*-
"""Tool get_recent_alerts — последние алерты из alert_events (partitioned).

Колонки схемы: alert_events.stage (warning/stop), .matched_rule_codes (jsonb),
.created_at; fb_ads.ad_name, .fb_ad_id.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import text

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError


class GetRecentAlertsTool:
    """Сводка алертов из alert_events за последние N часов.

    alert_events — partitioned by month, stage = 'warning' | 'stop'.
    """

    name: ClassVar[str] = "get_recent_alerts"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_recent_alerts",
        "description": (
            "Последние алерты системы (WARNING/STOP) с привязкой к ad_id "
            "и matched_rule_codes. Параметры: hours (по умолчанию 24), "
            "stage (warning/stop/null), limit (50)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "minimum": 1, "maximum": 168, "default": 24},
                "stage": {"type": "string", "enum": ["warning", "stop"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        engine = ctx.require_engine()
        try:
            hours = int(args.get("hours") or 24)
            limit = int(args.get("limit") or 50)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"hours/limit должны быть целыми: {exc}") from exc
        hours = max(1, min(hours, 168))
        limit = max(1, min(limit, 200))
        stage_arg = args.get("stage")
        stage = str(stage_arg).strip().lower() if stage_arg else None
        if stage and stage not in ("warning", "stop"):
            raise ToolError(f"stage должен быть 'warning' или 'stop', получено: {stage!r}")

        sql = (
            "SELECT ae.stage, ae.matched_rule_codes, ae.created_at, "
            "       a.fb_ad_id, a.ad_name "
            "FROM alert_events ae "
            "JOIN fb_ads a ON a.id = ae.ad_id "
            "WHERE ae.created_at >= NOW() - make_interval(hours => :hrs) "
        )
        params: dict[str, Any] = {"hrs": hours, "lim": limit}
        if stage:
            sql += "AND ae.stage = :stg "
            params["stg"] = stage
        sql += "ORDER BY ae.created_at DESC LIMIT :lim"

        async with engine.connect() as conn:
            rows = (await conn.execute(text(sql), params)).all()

        if not rows:
            return f"Алертов за последние {hours}ч нет."

        lines = [f"Алертов за последние {hours}ч: {len(rows)}"]
        for row in rows:
            stage_val, rule_codes, created_at, fb_ad_id, ad_name = row
            codes_str = ", ".join(rule_codes or []) if isinstance(rule_codes, list) else "?"
            ts = created_at.strftime("%Y-%m-%d %H:%M") if created_at else "?"
            short_name = (ad_name or "")[:48]
            lines.append(
                f"[{ts}] {str(stage_val).upper()} ad={fb_ad_id} «{short_name}» rules=({codes_str})"
            )
        return "\n".join(lines)
