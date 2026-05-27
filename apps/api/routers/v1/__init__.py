# -*- coding: utf-8 -*-
"""Auto-discovery роутеров FastAPI из пакета apps.api.routers.v1.

Функция `register_all` автоматически находит все Python-модули в этом пакете
и подключает к приложению роутеры с префиксом `/api`. Это позволяет добавлять
новые роутеры (например, settings_observer.py, offers.py) без правок main.py.

Условие подключения: модуль должен содержать атрибут `router` типа `APIRouter`.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRouter

logger = logging.getLogger(__name__)

# Пакет, в котором ищем роутеры.
_PACKAGE = "apps.api.routers.v1"
_PACKAGE_DIR = Path(__file__).parent


def register_all(app: FastAPI) -> None:
    """Находит все модули в apps.api.routers.v1 и регистрирует их роутеры.

    Для каждого модуля проверяет наличие атрибута `router: APIRouter`.
    Если атрибут есть — вызывает `app.include_router(router, prefix="/api")`.
    Логирует список зарегистрированных роутеров.
    """
    registered: list[str] = []
    skipped: list[str] = []

    for module_info in pkgutil.iter_modules([str(_PACKAGE_DIR)]):
        module_name = f"{_PACKAGE}.{module_info.name}"
        try:
            module = importlib.import_module(module_name)
        except Exception:
            logger.exception("Не удалось импортировать модуль роутера: %s", module_name)
            continue

        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            app.include_router(router, prefix="/api")
            registered.append(module_info.name)
        else:
            skipped.append(module_info.name)

    if registered:
        logger.info(
            "Зарегистрированы роутеры v1 (%d): %s",
            len(registered),
            ", ".join(registered),
        )
    if skipped:
        logger.debug(
            "Модули без атрибута `router` (пропущены): %s",
            ", ".join(skipped),
        )
