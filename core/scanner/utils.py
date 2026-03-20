from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from core.domain import DeliveryStatus

_EMPTY_MARKERS = {
    "",
    "na",
    "n/a",
    "—",
    "-",
    "нет",
    "нет данных",
    "не указано",
    "unknown",
}

_STATUS_MARKERS: tuple[tuple[DeliveryStatus, tuple[str, ...]], ...] = (
    (
        DeliveryStatus.NOT_DELIVERING,
        (
            "not delivering",
            "не показывается",
            "не доставляется",
            "не идёт",
            "не идет",
            "нет показов",
        ),
    ),
    (
        DeliveryStatus.PAUSED,
        (
            "paused",
            "приостановлено",
            "остановлено",
            "на паузе",
            "пауза",
            "off",
        ),
    ),
    (
        DeliveryStatus.LEARNING,
        (
            "learning",
            "обучение",
            "обучается",
        ),
    ),
    (
        DeliveryStatus.ACTIVE,
        (
            "active",
            "активно",
            "включено",
            "в работе",
            "работает",
            "running",
        ),
    ),
)


def _normalize_text(value: str) -> str:
    normalized = value.casefold().replace("\u00a0", " ").strip()
    normalized = re.sub(r"[^\w\s]+", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _is_missing_value(value: str) -> bool:
    compact = re.sub(r"[\W_]+", "", value.casefold().replace("\u00a0", " ").strip())
    return compact in _EMPTY_MARKERS


def normalize_delivery_status(raw_status: object | None) -> DeliveryStatus:
    """Нормализует сырой статус из UI в доменный статус доставки."""

    if raw_status is None:
        return DeliveryStatus.UNKNOWN

    if isinstance(raw_status, DeliveryStatus):
        return raw_status

    normalized = _normalize_text(str(raw_status))
    if not normalized or normalized in _EMPTY_MARKERS:
        return DeliveryStatus.UNKNOWN

    for status, markers in _STATUS_MARKERS:
        if any(marker in normalized for marker in markers):
            return status

    return DeliveryStatus.UNKNOWN


class StatusNormalizer:
    """Нормализует сырые статусы без привязки к DOM-локаторам."""

    @classmethod
    def normalize(cls, raw_status: object | None) -> DeliveryStatus:
        """Возвращает доменный статус для переданного сырого значения."""

        return normalize_delivery_status(raw_status)


def parse_scanner_decimal(raw_value: object | None) -> Decimal | None:
    """Парсит числовое значение или денежную строку в Decimal для scanner runtime."""

    if raw_value is None:
        return None

    if isinstance(raw_value, Decimal):
        return raw_value

    if isinstance(raw_value, bool):
        return None

    if isinstance(raw_value, int):
        return Decimal(raw_value)

    if isinstance(raw_value, float):
        return Decimal(str(raw_value))

    raw_text = str(raw_value).strip()
    if _is_missing_value(raw_text):
        return None

    candidate = (
        raw_text.replace("\u00a0", " ")
        .replace("$", "")
        .replace("€", "")
        .replace("₽", "")
        .replace("£", "")
        .replace("¥", "")
        .replace(" ", "")
        .replace("'", "")
    )

    negative = False
    if candidate.startswith("(") and candidate.endswith(")"):
        negative = True
        candidate = candidate[1:-1]

    if candidate.startswith("-"):
        negative = True
        candidate = candidate[1:]

    if candidate.startswith("+"):
        candidate = candidate[1:]

    if not candidate or _is_missing_value(candidate):
        return None

    if "," in candidate and "." in candidate:
        decimal_separator = "," if candidate.rfind(",") > candidate.rfind(".") else "."
        thousand_separator = "." if decimal_separator == "," else ","
        candidate = candidate.replace(thousand_separator, "")
        candidate = candidate.replace(decimal_separator, ".")
    elif "," in candidate:
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", candidate):
            candidate = candidate.replace(",", "")
        else:
            candidate = candidate.replace(",", ".")
    elif "." in candidate:
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", candidate):
            candidate = candidate.replace(".", "")

    candidate = re.sub(r"[^0-9.]+", "", candidate)
    if candidate.count(".") > 1:
        head, tail = candidate.rsplit(".", 1)
        candidate = head.replace(".", "") + "." + tail

    if not candidate:
        return None

    if negative:
        candidate = f"-{candidate}"

    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


__all__ = [
    "StatusNormalizer",
    "normalize_delivery_status",
    "parse_scanner_decimal",
]
