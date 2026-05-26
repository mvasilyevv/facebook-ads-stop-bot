# -*- coding: utf-8 -*-
"""Tool set_scanning — включение/выключение сканирования observer'а."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from core.ai_assistant.tools.base import RiskLevel, ToolError


class SetScanningTool:
    """Включить/выключить сканирование observer'а.

    Используй только если пользователь явно просит — это влияет на всю работу бота.
    """

    name: ClassVar[str] = "set_scanning"
    risk_level: ClassVar[RiskLevel] = RiskLevel.DRAFT_REQUIRED
    schema: ClassVar[dict[str, Any]] = {
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
    }

    async def run(self, args: dict[str, Any]) -> str:
        """PATCH /api/settings/observer/scanning."""
        from core.config import get_settings

        enabled = bool(args.get("enabled", False))
        settings = get_settings()
        url = f"http://127.0.0.1:{settings.api_port}/api/settings/observer/scanning"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["X-API-Key"] = settings.api_key

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.patch(
                    url, headers=headers, json={"is_scanning_enabled": enabled}
                )
        except httpx.HTTPError as exc:
            raise ToolError(f"set_scanning запрос не удался: {exc}") from exc

        if resp.status_code >= 400:
            raise ToolError(f"set_scanning HTTP {resp.status_code}: {resp.text[:200]}")
        return f"OK: сканирование {'включено' if enabled else 'выключено'}"
