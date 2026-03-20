from __future__ import annotations

import logging
from logging.config import dictConfig

from core.config import get_settings


def configure_logging() -> None:
    """Настраивает структурированное логирование для всех сервисов."""

    settings = get_settings()
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
                    "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "level": settings.log_level,
                }
            },
            "root": {"handlers": ["console"], "level": settings.log_level},
        }
    )
    logging.getLogger(__name__).info("Логирование настроено")
