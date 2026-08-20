# -*- coding: utf-8 -*-
"""Статус рекламного кабинета Meta как отдельное подтверждаемое свидетельство.

Marketing API отдаёт ``account_status`` числом. Активным считается ровно один
код: всё остальное — либо названная причина отказа, либо неизвестность. Кабинет
без подтверждённого статуса не считается активным: ``null`` означает
«неизвестно», а не «работает».
"""

from __future__ import annotations

from typing import Final

# Единственный код, при котором Meta разрешает создавать и редактировать рекламу.
ACCOUNT_STATUS_ACTIVE: Final = 1

# Значения перечисления Marketing API. 201/202 — фильтры выборки, а не состояния
# конкретного кабинета; они попадают сюда только ради читаемого имени в логе.
ACCOUNT_STATUS_NAMES: Final[dict[int, str]] = {
    1: "ACTIVE",
    2: "DISABLED",
    3: "UNSETTLED",
    7: "PENDING_RISK_REVIEW",
    8: "PENDING_SETTLEMENT",
    9: "IN_GRACE_PERIOD",
    100: "PENDING_CLOSURE",
    101: "CLOSED",
    201: "ANY_ACTIVE",
    202: "ANY_CLOSED",
}

# Ширина smallint в снимке: значение вне диапазона не свидетельство, а мусор.
_MAX_PERSISTED_STATUS: Final = 32767


def validated_account_status(raw: object) -> int | None:
    """Вернуть подтверждённый код статуса или None, если его нет.

    Незнакомый код сохраняется как есть: он неактивен, и это уже решение. А вот
    ``None``, ноль, отрицательное значение, ``bool`` и любая нечисловая строка —
    это отсутствие свидетельства, и подменять его активностью нельзя.
    """

    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text.isdigit():
            return None
        value = int(text)
    else:
        return None
    if value <= 0 or value > _MAX_PERSISTED_STATUS:
        return None
    return value


def account_status_name(status: int | None) -> str:
    """Имя статуса для лога; неизвестный код остаётся числом, а не «активен»."""

    if status is None:
        return "UNKNOWN"
    return ACCOUNT_STATUS_NAMES.get(status, str(status))


__all__ = [
    "ACCOUNT_STATUS_ACTIVE",
    "ACCOUNT_STATUS_NAMES",
    "account_status_name",
    "validated_account_status",
]
