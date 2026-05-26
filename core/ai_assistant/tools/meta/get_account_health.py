# -*- coding: utf-8 -*-
"""Tool get_account_health — состояние Marketing API канала."""

from __future__ import annotations

from typing import Any, ClassVar

from clients.python_grpc.meta_api_client import MetaApiError
from core.ai_assistant.tools.base import RiskLevel
from core.meta_api.client import MetaApiHighLevelClient


class GetAccountHealthTool:
    """Проверить состояние Marketing API канала.

    Возвращает:
    - Статус Vision-сессии и наличие EAA-токена
    - Headroom rate-limit за последние N минут (из аудит-лога)
    - Список последних ошибок
    """

    name: ClassVar[str] = "get_account_health"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_account_health",
        "description": (
            "Состояние Marketing API канала: токен (валиден/нет), "
            "rate-limit headroom, последние ошибки."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {
                    "type": "string",
                    "description": "ID кабинета для фильтрации аудит-лога (необязательно)",
                },
                "window_minutes": {
                    "type": "integer",
                    "default": 15,
                    "minimum": 1,
                    "maximum": 60,
                },
            },
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Собрать данные о здоровье канала и вернуть текстовый отчёт."""
        raw_account_id: str | None = args.get("ad_account_id")
        if raw_account_id:
            raw_account_id = raw_account_id.strip()
            if raw_account_id and not raw_account_id.startswith("act_"):
                raw_account_id = f"act_{raw_account_id}"

        window_minutes = int(args.get("window_minutes", 15))
        window_minutes = max(1, min(60, window_minutes))

        # 1. Проверяем Vision-сессию и токен через MetaApiHighLevelClient.health()
        health_info = await _get_health_info()

        # 2. Читаем статистику rate-limit из аудит-лога (best-effort)
        rate_limit_stats = await _get_rate_limit_stats(raw_account_id, window_minutes)

        # 3. Читаем последние ошибки из аудит-лога (best-effort)
        recent_errors = await _get_recent_errors()

        return _format_health_report(
            health_info, rate_limit_stats, recent_errors, window_minutes, raw_account_id
        )


# ── Вспомогательные функции ──────────────────────────────────────────────────


async def _get_health_info() -> dict[str, Any]:
    """Получить статус Marketing API канала через MetaApiHighLevelClient."""
    try:
        async with MetaApiHighLevelClient() as client:
            health = await client.health()
            return {
                "healthy": health.healthy,
                "token_present": health.token_present,
                "token_length": health.token_length,
                "current_url": health.current_url,
                "detail": health.detail,
                "error": None,
            }
    except MetaApiError as exc:
        return {
            "healthy": False,
            "token_present": False,
            "token_length": 0,
            "current_url": "",
            "detail": str(exc),
            "error": f"MetaApiError(code={exc.code}): {exc}",
        }
    except Exception as exc:
        return {
            "healthy": False,
            "token_present": False,
            "token_length": 0,
            "current_url": "",
            "detail": str(exc),
            "error": str(exc),
        }


async def _get_rate_limit_stats(
    ad_account_id: str | None,
    window_minutes: int,
) -> dict[str, Any] | None:
    """Получить статистику rate-limit через query_rate_limit_headroom (best-effort)."""
    try:
        from core.db import async_session_factory
        from core.meta_api.audit import query_rate_limit_headroom

        async with async_session_factory() as db:
            stats = await query_rate_limit_headroom(
                db,
                ad_account_id=ad_account_id,
                window_minutes=window_minutes,
            )
            return stats
    except Exception:
        # Аудит недоступен — не критично
        return None


async def _get_recent_errors() -> list[str]:
    """Получить последние ошибки из аудит-лога (best-effort)."""
    try:
        from datetime import UTC, datetime, timedelta

        from core.db import async_session_factory
        from core.meta_api.audit import query_recent_errors

        since = datetime.now(UTC) - timedelta(minutes=60)

        async with async_session_factory() as db:
            errors = await query_recent_errors(db, since=since, limit=5)
            return [
                f"{e.method} {e.endpoint} → status={e.response_status} error_code={e.error_code}"
                for e in errors
            ]
    except Exception:
        # Аудит недоступен — не критично
        return []


def _format_health_report(
    health: dict[str, Any],
    rate_stats: dict[str, Any] | None,
    recent_errors: list[str],
    window_minutes: int,
    ad_account_id: str | None,
) -> str:
    """Отформатировать данные о состоянии канала в читаемый текст."""
    status_icon = "HEALTHY" if health["healthy"] else "UNHEALTHY"
    token_status = "присутствует" if health["token_present"] else "ОТСУТСТВУЕТ"

    lines = [
        f"Marketing API канал: {status_icon}",
        f"  Токен:        {token_status} (длина={health['token_length']})",
        f"  URL сессии:   {health['current_url'] or 'n/a'}",
        f"  Детали:       {health['detail']}",
    ]

    if health["error"]:
        lines.append(f"  Ошибка gRPC:  {health['error']}")

    lines.append("")

    # Rate-limit статистика
    account_filter = f" (кабинет {ad_account_id})" if ad_account_id else ""
    lines.append(f"Rate-limit за последние {window_minutes} мин{account_filter}:")
    if rate_stats is not None:
        total = rate_stats.get("total_calls", 0)
        rl = rate_stats.get("rate_limited_calls", 0)
        err = rate_stats.get("errored_calls", 0)
        avg_dur = rate_stats.get("average_duration_ms", 0)
        lines.append(f"  Всего вызовов:       {total}")
        lines.append(f"  Rate-limited:        {rl}")
        lines.append(f"  Ошибок (не rl):      {err}")
        lines.append(f"  Среднее время:       {avg_dur} мс")
    else:
        lines.append("  Аудит-лог недоступен (БД не подключена или таблица отсутствует)")

    lines.append("")

    # Последние ошибки
    lines.append("Последние ошибки (до 5):")
    if recent_errors:
        for e in recent_errors:
            lines.append(f"  • {e}")
    else:
        lines.append("  Ошибок не найдено или аудит недоступен")

    return "\n".join(lines)
