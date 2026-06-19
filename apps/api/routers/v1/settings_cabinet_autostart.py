# -*- coding: utf-8 -*-
"""FastAPI роутер настроек автостарта кабинета (admin-зона дашборда).

Endpoints под /api (auto-discovery с prefix="/api"):
- GET /settings/cabinet-autostart — текущий конфиг (system_config).
- PUT /settings/cabinet-autostart — полная замена конфига.

Конфиг читается воркером cabinet_scheduler раз в минуту — рестарт не нужен.
TMA (mini) ходит на отдельный owner-gated /tma/cabinet-autostart.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.cabinet_autostart import (
    CabinetAutostartPutRequest,
    CabinetAutostartResponse,
)
from core.scheduler.cabinet_autostart import read_autostart_config, write_autostart_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/cabinet-autostart", tags=["settings"])


@router.get("", response_model=CabinetAutostartResponse)
async def get_cabinet_autostart(engine: DepEngine) -> CabinetAutostartResponse:
    """Текущий конфиг автостарта (или дефолт, если ещё не задан)."""
    cfg = await read_autostart_config(engine)
    return CabinetAutostartResponse.from_config(cfg)


@router.put("", response_model=CabinetAutostartResponse)
async def put_cabinet_autostart(
    body: CabinetAutostartPutRequest,
    engine: DepEngine,
) -> CabinetAutostartResponse:
    """Полная замена конфига автостарта. Применяется со следующей минуты воркера."""
    await write_autostart_config(
        engine,
        {
            "enabled": body.enabled,
            "hour_utc": body.hour_utc,
            "minute_utc": body.minute_utc,
        },
    )
    logger.info(
        "cabinet_autostart обновлён (web): enabled=%s %02d:%02d UTC",
        body.enabled,
        body.hour_utc,
        body.minute_utc,
    )
    cfg = await read_autostart_config(engine)
    return CabinetAutostartResponse.from_config(cfg)
