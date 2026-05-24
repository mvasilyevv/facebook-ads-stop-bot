# -*- coding: utf-8 -*-
"""Единая инициализация логирования через structlog.

Поддерживает два режима:
- json  (default) — JSON-строки для Loki/ELK, processor chain с ISO timestamp.
- text            — ConsoleRenderer для локальной разработки.

Переменная окружения LOG_FORMAT=json|text управляет режимом.

Интеграция со stdlib logging:
    Все существующие logging.getLogger(__name__).info(...) тоже проходят
    через structlog ProcessorFormatter и получают те же JSON-поля (worker,
    scan_id, timestamp и т.д.).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, unbind_contextvars  # noqa: F401 — re-export

__all__ = [
    "setup_logging",
    "bind_contextvars",
    "unbind_contextvars",
]

# Processors для pre-chain — вызываются до рендера даже для stdlib-записей.
# Для stdlib-записей structlog сначала применяет pre-chain, потом processor chain.
# Примечание: add_logger_name здесь не используем — он ломается с PrintLogger,
# а для stdlib-записей имя логгера попадает через ProcessorFormatter автоматически.
_PRE_CHAIN: list[Any] = [
    # Переносим contextvars-поля в event dict (worker, scan_id, ad_id, ...)
    structlog.contextvars.merge_contextvars,
    # Добавляем уровень логирования
    structlog.stdlib.add_log_level,
    # ISO timestamp
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    # stack_info в строку
    structlog.processors.StackInfoRenderer(),
    # exc_info → вложенный dict с трейсом
    structlog.processors.format_exc_info,
]


def _build_json_renderer() -> list[Any]:
    """Финальные процессоры для JSON-рендеринга."""
    return [
        # Трейсбек как dict (Loki/ELK дружественно)
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(),
    ]


def _build_text_renderer() -> list[Any]:
    """Финальные процессоры для текстового рендеринга (local dev)."""
    return [
        structlog.dev.ConsoleRenderer(colors=True),
    ]


def _configure_stdlib_logging(level: int, *, renderer: list[Any], use_structlog: bool) -> None:
    """Настраивает stdlib logging.

    При use_structlog=True — хэндлер с ProcessorFormatter, который прогоняет
    все stdlib-записи через structlog pre_chain + renderer.
    При use_structlog=False — стандартный текстовый формат для local dev.
    """
    root = logging.getLogger()
    # Убираем все старые хэндлеры (нас интересует только один выход)
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()

    if use_structlog:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                # foreign_pre_chain — применяется к записям от stdlib-логгеров
                foreign_pre_chain=_PRE_CHAIN,
                # processors — финальная цепочка (уже после pre_chain)
                processors=[
                    # Убираем служебное поле _record, которое ProcessorFormatter добавляет
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    *renderer,
                ],
            )
        )
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
        )

    root.addHandler(handler)
    root.setLevel(level)
    # Снижаем шум от uvicorn access logs (они дублируют middleware-логи)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def setup_logging(
    worker_name: str,
    *,
    log_format: str | None = None,
    log_level: str = "INFO",
) -> None:
    """Единая точка инициализации логирования для всех воркеров и API.

    Args:
        worker_name: Имя воркера/сервиса (инжектируется как поле ``worker`` в каждом логе).
        log_format:  Формат вывода: ``"json"`` | ``"text"``.
                     Если None — читается из env LOG_FORMAT, дефолт ``"json"``.
        log_level:   Уровень логирования (INFO, DEBUG, WARNING, ...).
    """
    # Определяем формат
    effective_format = (log_format or os.getenv("LOG_FORMAT", "json")).strip().lower()
    use_json = effective_format != "text"

    level = logging.getLevelName(log_level.upper())
    if not isinstance(level, int):
        level = logging.INFO

    if use_json:
        renderer = _build_json_renderer()
    else:
        renderer = _build_text_renderer()

    # Настраиваем stdlib logging → structlog bridge
    _configure_stdlib_logging(level, renderer=renderer, use_structlog=use_json)

    # Настраиваем structlog (для кода, который использует structlog.get_logger напрямую)
    structlog.configure(
        processors=[
            *_PRE_CHAIN,
            *renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Инжектируем имя воркера в contextvars (появится в каждом лог-событии)
    bind_contextvars(worker=worker_name)
