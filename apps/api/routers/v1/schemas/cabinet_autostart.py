# -*- coding: utf-8 -*-
"""Pydantic-схемы автостарта кабинета по расписанию (cabinet_autostart).

Конфиг живёт в system_config (key='cabinet_autostart'); pure I/O —
core.scheduler.cabinet_autostart. Money-критично: в заданное время воркер
включает объявления своих кампаний с нужной датой в названии без подтверждения.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# Метка-дата кампании: ДД.ММ (опц. .ГГ/.ГГГГ). Совпадает с тем, как дата стоит в
# названии кампании (напр. «… | 22.05»). Строгая проверка — ловим опечатки в UI;
# на безопасность не влияет (неверная дата → автостарт ничего не включит).
_DATE_RE = re.compile(r"^\d{1,2}\.\d{1,2}(\.\d{2,4})?$")


class CabinetAutostartResponse(BaseModel):
    """Текущий конфиг автостарта."""

    enabled: bool
    hour_utc: int
    minute_utc: int
    dates: list[str] = Field(default_factory=list)

    @classmethod
    def from_config(cls, cfg: dict) -> "CabinetAutostartResponse":
        """Снимок dict-конфига (core.scheduler.cabinet_autostart) в response-схему."""
        return cls(
            enabled=bool(cfg.get("enabled", False)),
            hour_utc=int(cfg.get("hour_utc", 6)),
            minute_utc=int(cfg.get("minute_utc", 0)),
            dates=list(cfg.get("dates") or []),
        )


class CabinetAutostartPutRequest(BaseModel):
    """Тело PUT — полная замена конфига автостарта."""

    enabled: bool
    hour_utc: int = Field(..., ge=0, le=23, description="Час запуска (UTC) 0..23")
    minute_utc: int = Field(..., ge=0, le=59, description="Минута запуска 0..59")
    dates: list[str] = Field(default_factory=list, description="Даты-метки кампаний (ДД.ММ)")

    @field_validator("dates")
    @classmethod
    def validate_dates(cls, v: list[str]) -> list[str]:
        """Каждая дата — ДД.ММ[.ГГ]; трим, дедуп с сохранением порядка."""
        seen: set[str] = set()
        out: list[str] = []
        for raw in v:
            s = str(raw).strip()
            if not s:
                continue
            if not _DATE_RE.match(s):
                raise ValueError(f"дата {s!r} — ожидается формат ДД.ММ (напр. 22.05)")
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out
