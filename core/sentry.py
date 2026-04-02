# -*- coding: utf-8 -*-
"""Инициализация Sentry SDK для всех точек входа (API + воркеры)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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
            traces_sample_rate=0.1,
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
        )
        logger.info("Sentry инициализирован (environment=%s)", environment)
    except ImportError:
        logger.warning("sentry-sdk не установлен — пропускаем инициализацию Sentry")
