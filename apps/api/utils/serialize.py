# -*- coding: utf-8 -*-
"""Примитивы сериализации значений из БД в JSON-дружелюбный формат.

Единый источник преобразований NUMERIC для действующих API-контрактов.

Decimal сериализуется как str — стабильно и без потери точности (float-кодек
Pydantic для NUMERIC роняет точность либо требует отдельной настройки).
"""

from __future__ import annotations

from typing import Any


def decimal_str(value: Any) -> str | None:
    """Decimal/NUMERIC → str без потери точности. None → None."""
    if value is None:
        return None
    return str(value)


def int_or_none(value: Any) -> int | None:
    """Числовое значение → int. None → None."""
    if value is None:
        return None
    return int(value)


__all__ = ["decimal_str", "int_or_none"]
