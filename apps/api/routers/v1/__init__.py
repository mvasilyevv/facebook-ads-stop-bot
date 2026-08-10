# -*- coding: utf-8 -*-
"""Fail-fast registry for the versioned FastAPI surface.

Every production router is named explicitly. A missing import, a module
without ``router: APIRouter`` or an unregistered router file prevents API
startup instead of silently shipping a partial control plane.
"""

from __future__ import annotations

import importlib
import logging

from fastapi import FastAPI
from fastapi.routing import APIRouter

logger = logging.getLogger(__name__)

ROUTER_MODULES: tuple[str, ...] = (
    "adset_duplicates",
    "ai_analyze",
    "ai_chat_web",
    "alertmanager_webhook",
    "analytics",
    "browser_operations_internal",
    "campaigns_create",
    "campaigns_meta",
    "desktop",
    "offers",
    "operator",
    "operator_preferences",
    "settings_observer",
    "settings_telegram",
    "settings_vision",
    "telegram_webhook",
    "tma",
    "tools",
)

_PACKAGE = "apps.api.routers.v1"


def register_all(app: FastAPI) -> None:
    """Import and register the complete reviewed router set."""

    for module_name in ROUTER_MODULES:
        qualified_name = f"{_PACKAGE}.{module_name}"
        module = importlib.import_module(qualified_name)
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            raise RuntimeError(f"{qualified_name} must expose router: APIRouter")
        app.include_router(router, prefix="/api")

    logger.info(
        "Зарегистрирован полный набор роутеров v1 (%d): %s",
        len(ROUTER_MODULES),
        ", ".join(ROUTER_MODULES),
    )


__all__ = ["ROUTER_MODULES", "register_all"]
