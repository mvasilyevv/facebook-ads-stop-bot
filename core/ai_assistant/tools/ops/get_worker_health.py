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
from core.observer.queries import load_scanning_enabled
from core.observer.runtime import read_observer_runtime

logger = logging.getLogger(__name__)

# Канонический список ожидаемых воркеров — зеркало
# apps/health_watchdog/main.py::DEFAULT_EXPECTED_WORKERS (11 воркеров).
# Синхронизация защищена контрактным тестом (test_heartbeat_contract.py).
# Раньше здесь было только 5 имён → tool «не видел» money-критичные
# cabinet_scheduler/tracker_aggregator и врал про здоровье системы.
# Публичное имя: переиспользуется MCP-ресурсом workers-health (apps/mcp_server).
EXPECTED_WORKERS: tuple[str, ...] = (
    "observer",
    "telegram_poller",
    "cleanup",
    "reconciler",
    "meta_api",
    "tracker_aggregator",
    "enable_reco",
    "cabinet_scheduler",
    "digest_scheduler",
    "creator",
    "creator_recorder",
    # campaign_creator — залив FB-кампаний из UI (always-on compose-сервис); его
    # зависание оставляет launch'и в pending без исполнения → мониторим как creator.
    "campaign_creator",
    # browser-agent — нативный host-сервис (мост к Vision), пишет heartbeat (rank 1).
    "browser-agent",
)

# Обратная совместимость для старых импортов.
_EXPECTED_WORKERS = EXPECTED_WORKERS


class GetWorkerHealthTool:
    """Читает worker:heartbeat:* из Redis и возвращает сводку."""

    name: ClassVar[str] = "get_worker_health"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_worker_health",
        "description": (
            "Статус ожидаемых воркеров: читает worker:heartbeat:* из Redis (TTL 60s), "
            "а для observer отдельно проверяет runtime и глобальный флаг сканирования. "
            "Воркер без ключа = не пишет heartbeat (упал или не запущен). "
            "Живой heartbeat observer НЕ означает, что сканирование включено. "
            "Money-критичные: observer (скан и авто-стоп), meta_api (исполнение pause/enable), "
            "cabinet_scheduler (автостарт кабинета по расписанию). Если они мертвы — "
            "сообщи пользователю это В ПЕРВУЮ очередь."
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

        runtime = await read_observer_runtime(redis_client)
        scanning_enabled: bool | None = None
        if ctx.engine is not None:
            try:
                scanning_enabled = await load_scanning_enabled(ctx.engine)
            except Exception as exc:  # noqa: BLE001 — heartbeat-диагностика остаётся полезной
                logger.warning("load_scanning_enabled failed: %s", exc)

        lines.extend(
            [
                "",
                "Операционный статус observer:",
                f"- runtime={runtime['status']}",
                f"- scanning_enabled={scanning_enabled if scanning_enabled is not None else 'unknown'}",
                f"- last_successful_scan_at={runtime['last_successful_scan_at'] or 'unknown'}",
                f"- next_scan_at={runtime['next_scan_at'] or 'unknown'}",
            ]
        )
        if runtime["status"] == "paused" or scanning_enabled is False:
            lines.append(
                "- ВАЖНО: процесс observer жив, но сканирование остановлено; "
                "это нельзя описывать как «всё работает штатно»."
            )
        return "\n".join(lines)
