# -*- coding: utf-8 -*-
"""Авто-диагностика критических алертов через AI."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.ai_assistant.prompts import SYSTEM_PROMPT_DIAGNOSTICS
from core.config import get_settings
from core.safe_diagnostics import redact_sensitive_text, safe_exception_diagnostic

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOGS_DIR = _REPO_ROOT / ".logs"

# Кулдаун: одна и та же диагностика не чаще раза в N секунд (на ключ).
_last_diagnose_at: dict[str, float] = {}


def _read_log_tail(log_name: str, lines: int) -> str:
    """Читает хвост лога. Возвращает пустую строку при ошибке."""
    if not log_name:
        return ""
    log_path = _LOGS_DIR / log_name
    try:
        if not log_path.exists():
            return ""
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-lines:]
        return "".join(tail)
    except OSError:
        return ""


_LOG_HINTS_BY_KEY: dict[str, str] = {
    "observer": "observer.log",
    "telegram_delivery": "telegram_delivery_worker.log",
    "telegram_updates": "telegram_update_worker.log",
    "telegram": "telegram_delivery_worker.log",
    "meta_api": "meta_api_worker.log",
    "browser_agent": "browser_agent.log",
}


def _guess_log_name(alert_key: str) -> str:
    """Эвристика: по alert_key подобрать имя лога."""
    lower = alert_key.lower()
    for hint, log in _LOG_HINTS_BY_KEY.items():
        if hint in lower:
            return log
    return "health_watchdog.log"


async def diagnose_alert(
    *,
    alert_key: str,
    context: str = "",
    log_name: str | None = None,
    timeout_seconds: float | None = None,
) -> str | None:
    """Запросить у AI краткий диагноз. Возвращает HTML или None.

    None — если AI недоступен, в кулдауне или ответ пустой.
    """
    settings = get_settings()
    if not settings.ai_diagnostics_enabled:
        return None

    cooldown = float(settings.ai_diagnostics_cooldown_seconds)
    last = _last_diagnose_at.get(alert_key, 0.0)
    if time.monotonic() - last < cooldown:
        logger.debug("AI diagnose: cooldown активен для %s", alert_key)
        return None

    client = get_ai_client(settings)
    if not client.is_available:
        return None

    actual_log = log_name or _guess_log_name(alert_key)
    log_excerpt = _read_log_tail(actual_log, settings.ai_max_log_lines)
    if len(log_excerpt) > 8000:
        log_excerpt = log_excerpt[-8000:]
    log_excerpt = redact_sensitive_text(log_excerpt)

    user_text = (
        f"alert_key: {redact_sensitive_text(alert_key)}\n"
        f"context: {redact_sensitive_text(context) or '(нет)'}\n"
        f"log_file: {actual_log}\n"
        f"log_excerpt (последние строки):\n```\n{log_excerpt or '(лог пуст или недоступен)'}\n```"
    )

    try:
        result = await asyncio.wait_for(
            client.chat(
                messages=[{"role": "user", "content": user_text}],
                system=SYSTEM_PROMPT_DIAGNOSTICS,
                max_tokens=400,
            ),
            timeout=timeout_seconds or float(settings.ai_timeout_seconds),
        )
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning("AI diagnose: timeout для %s", redact_sensitive_text(alert_key))
        return None
    except AIUnavailableError as exc:
        logger.warning(
            "AI diagnose: недоступен (%s)",
            safe_exception_diagnostic(exc),
        )
        return None

    text = redact_sensitive_text(result.text).strip()
    if not text:
        return None

    _last_diagnose_at[alert_key] = time.monotonic()
    return text


def reset_diagnose_cooldown_for_tests() -> None:
    """Сбросить кулдаун между тестами."""
    _last_diagnose_at.clear()
