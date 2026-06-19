# -*- coding: utf-8 -*-
"""Pydantic-схемы автостарта кабинета по расписанию (cabinet_autostart).

Конфиг живёт в system_config (key='cabinet_autostart'); pure I/O —
core.scheduler.cabinet_autostart. Money-критично: в заданное время воркер
включает объявления ОТСЛЕЖИВАЕМЫХ кампаний (observer_config.campaign_ids) без
подтверждения. Список кампаний не дублируется — берётся из allowlist'а.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CabinetAutostartResponse(BaseModel):
    """Текущий конфиг автостарта (только расписание; кампании — в allowlist'е)."""

    enabled: bool
    hour_utc: int
    minute_utc: int

    @classmethod
    def from_config(cls, cfg: dict) -> "CabinetAutostartResponse":
        """Снимок dict-конфига (core.scheduler.cabinet_autostart) в response-схему."""
        return cls(
            enabled=bool(cfg.get("enabled", False)),
            hour_utc=int(cfg.get("hour_utc", 6)),
            minute_utc=int(cfg.get("minute_utc", 0)),
        )


class CabinetAutostartPutRequest(BaseModel):
    """Тело PUT — расписание автостарта (вкл/выкл + время)."""

    enabled: bool
    hour_utc: int = Field(..., ge=0, le=23, description="Час запуска (UTC) 0..23")
    minute_utc: int = Field(..., ge=0, le=59, description="Минута запуска 0..59")
