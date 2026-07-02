# -*- coding: utf-8 -*-
"""Инициализация Sentry SDK для всех точек входа (API + воркеры)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Названия переменных, значения которых нужно скрыть в событиях Sentry (H-6, п.4).
# Сравнение в _mask_sensitive_data идёт через .lower(), поэтому регистр здесь не важен —
# держим нижний регистр как канонический. Список соответствует всем SecretStr-полям
# core.config.Settings (см. H-6): пароли/токены/API-ключи/секреты постбэков.
_SENSITIVE_KEYS = frozenset(
    {
        "telegram_bot_token",
        "encryption_key",
        "encryption_key_verify",
        "api_key",
        "vision_x_token",
        "sentry_dsn",
        "tma_session_secret",
        "anthropic_api_key",
        "openai_api_key",
        "adsetpro_mcp_key",
        "adsetpro_postback_secret",
        "syntx_auth_token",
        "postgres_password",
        # Env-var формы (UPPER_CASE) — сравнение через .lower(), но держим явно
        # для читаемости и на случай прямого поиска по коду.
        "TELEGRAM_BOT_TOKEN",
        "ENCRYPTION_KEY",
        "ENCRYPTION_KEY_VERIFY",
        "API_KEY",
        "VISION_X_TOKEN",
        "SENTRY_DSN",
        "TMA_SESSION_SECRET",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "ADSETPRO_MCP_KEY",
        "ADSETPRO_POSTBACK_SECRET",
        "SYNTX_AUTH_TOKEN",
        "POSTGRES_PASSWORD",
        # Составные URL с потенциальными кредами (userinfo) — не SecretStr, но маскируем.
        "database_url",
        "redis_url",
        "DATABASE_URL",
        "REDIS_URL",
    }
)


def _mask_sensitive_data(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Фильтр before_send: маскирует чувствительные переменные окружения и extra-поля.

    Args:
        event: Сырое событие Sentry.
        hint: Дополнительный контекст (исключение и т.д.).

    Returns:
        Изменённое событие или None (отбросить событие).
    """
    # Маскируем переменные окружения в контексте запроса
    extra = event.get("extra", {})
    for key in list(extra.keys()):
        if key.lower() in {k.lower() for k in _SENSITIVE_KEYS}:
            extra[key] = "***"

    # Маскируем переменные окружения в контексте os.environ
    contexts = event.get("contexts", {})
    runtime_env = contexts.get("runtime", {}).get("env", {})
    for key in list(runtime_env.keys()):
        if key.lower() in {k.lower() for k in _SENSITIVE_KEYS}:
            runtime_env[key] = "***"

    return event


def setup_sentry(dsn: str, environment: str = "production", release: str | None = None) -> None:
    """Инициализирует Sentry SDK, если передан DSN.

    Args:
        dsn: Sentry DSN. Если пустая строка — Sentry не инициализируется.
        environment: Название окружения (production, staging, dev).
        release: Версия релиза (опционально, например git commit hash).
    """
    if not dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            # Трассировка 10% запросов для performance monitoring
            traces_sample_rate=0.1,
            # Профилирование 10% трассируемых транзакций
            profiles_sample_rate=0.1,
            integrations=[
                AsyncioIntegration(),
                SqlalchemyIntegration(),
                LoggingIntegration(
                    level=logging.WARNING,
                    event_level=logging.ERROR,
                ),
            ],
            # Не отправлять PII (IP-адреса, заголовки запросов)
            send_default_pii=False,
            # Фильтр для маскировки чувствительных данных
            before_send=_mask_sensitive_data,
        )
        logger.info("Sentry инициализирован (environment=%s)", environment)
    except ImportError:
        logger.warning("sentry-sdk не установлен — пропускаем инициализацию Sentry")
