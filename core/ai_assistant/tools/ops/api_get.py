# -*- coding: utf-8 -*-
"""Tool api_get — GET-запрос к внутреннему API."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from core.ai_assistant.tools.base import RiskLevel, ToolError


class ApiGetTool:
    """GET-запрос к внутреннему API на 127.0.0.1:8100.

    Только GET, путь должен начинаться на /api/.
    """

    name: ClassVar[str] = "api_get"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
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
    }

    async def run(self, args: dict[str, Any]) -> str:
        """GET к внутреннему API на 127.0.0.1:8100."""
        from core.config import get_settings

        path = str(args.get("path", ""))
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
