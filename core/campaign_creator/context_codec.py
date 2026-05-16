# -*- coding: utf-8 -*-
"""Сериализация StepContext ↔ dict для хранения в БД (JSONB).

Позволяет run-step / resume загружать контекст из БД без повторного ввода полей.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .steps.base import AdsetSpec, StepContext


def context_to_dict(ctx: StepContext) -> dict[str, Any]:
    """Развернуть StepContext в простой dict, пригодный для JSON."""
    data = asdict(ctx)
    # asdict уже разворачивает вложенные dataclass'ы, но extra может содержать
    # нерекурсивные значения — оставляем как есть.
    return data


def context_from_dict(data: dict[str, Any]) -> StepContext:
    """Восстановить StepContext из dict, прочитанного из БД."""
    payload = dict(data)
    raw_adsets = payload.pop("adsets", []) or []
    adsets = [AdsetSpec(**a) for a in raw_adsets]
    return StepContext(adsets=adsets, **payload)
