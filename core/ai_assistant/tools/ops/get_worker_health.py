# -*- coding: utf-8 -*-
"""Tool get_worker_health — heartbeat'ы воркеров из Redis.

Ключи Redis: worker:heartbeat:<name> с TTL 60s. Если ключа нет — воркер не пишет heartbeat
(возможно крашнулся или не запущен).
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolContext

logger = logging.getLogger(__name__)

# Список ожидаемых воркеров — для отображения "не работает" если ключа нет в Redis.
# disable/enable удалены: отключение/включение рекламы теперь через Marketing API
# (meta_api), отдельных DOM-toggle воркеров больше нет.
_EXPECTED_WORKERS: tuple[str, ...] = (
    "observer",
    "telegram_poller",
    "cleanup",
    "reconciler",
    "meta_api",
)


class GetWorkerHealthTool:
    """Читает worker:heartbeat:* из Redis и возвращает сводку."""

    name: ClassVar[str] = "get_worker_health"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_worker_health",
        "description": (
            "Статус всех воркеров: читает worker:heartbeat:* из Redis (TTL 60s). "
            "Воркер без ключа = не пишет heartbeat (упал или не запущен)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        redis_client = ctx.require_redis()

        results: list[tuple[str, str]] = []  # (worker_name, status_line)
        for worker in _EXPECTED_WORKERS:
            key = f"worker:heartbeat:{worker}"
            try:
                raw = await redis_client.get(key)
            except Exception as exc:
                logger.warning("redis.get(%s) failed: %s", key, exc)
                results.append((worker, "Redis недоступен"))
                continue

            if raw is None:
                results.append((worker, "не пишет heartbeat (отсутствует или TTL истёк)"))
                continue

            payload_str = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
            try:
                payload = json.loads(payload_str)
            except (ValueError, TypeError):
                payload = {"raw": payload_str[:120]}

            ts = payload.get("ts") or payload.get("last_seen") or payload.get("timestamp")
            extra: list[str] = []
            if ts:
                extra.append(f"ts={ts}")
            if "iteration" in payload:
                extra.append(f"iter={payload['iteration']}")
            if "version" in payload:
                extra.append(f"v={payload['version']}")
            results.append((worker, "ok " + " ".join(extra) if extra else "ok"))

        lines = ["Состояние воркеров (heartbeat в Redis):"]
        for name_, status in results:
            lines.append(f"- {name_}: {status}")
        return "\n".join(lines)
