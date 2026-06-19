# -*- coding: utf-8 -*-
"""Pydantic-схемы автостарта кабинета по расписанию (cabinet_autostart).

Конфиг живёт в system_config (key='cabinet_autostart'); pure I/O —
core.scheduler.cabinet_autostart. Money-критично: в заданное время воркер
включает объявления выбранных кампаний (галочками в UI) без подтверждения.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# Meta campaign ID — числовая строка (как в allowlist observer'а). Проверяем формат,
# чтобы не записать мусор; на безопасность не влияет (несуществующий id → ничего).
_CAMPAIGN_ID_RE = re.compile(r"^\d{1,32}$")


class CabinetAutostartResponse(BaseModel):
    """Текущий конфиг автостарта."""

    enabled: bool
    hour_utc: int
    minute_utc: int
    campaign_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_config(cls, cfg: dict) -> "CabinetAutostartResponse":
        """Снимок dict-конфига (core.scheduler.cabinet_autostart) в response-схему."""
        return cls(
            enabled=bool(cfg.get("enabled", False)),
            hour_utc=int(cfg.get("hour_utc", 6)),
            minute_utc=int(cfg.get("minute_utc", 0)),
            campaign_ids=list(cfg.get("campaign_ids") or []),
        )


class CabinetAutostartPutRequest(BaseModel):
    """Тело PUT — полная замена конфига автостарта."""

    enabled: bool
    hour_utc: int = Field(..., ge=0, le=23, description="Час запуска (UTC) 0..23")
    minute_utc: int = Field(..., ge=0, le=59, description="Минута запуска 0..59")
    campaign_ids: list[str] = Field(default_factory=list, description="Meta-ID выбранных кампаний")

    @field_validator("campaign_ids")
    @classmethod
    def validate_campaign_ids(cls, v: list[str]) -> list[str]:
        """Каждый id — числовой Meta-ID; трим, дедуп с сохранением порядка."""
        seen: set[str] = set()
        out: list[str] = []
        for raw in v:
            s = str(raw).strip()
            if not s:
                continue
            if not _CAMPAIGN_ID_RE.match(s):
                raise ValueError(f"campaign_id {s!r} — ожидается числовой Meta-ID кампании")
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out
