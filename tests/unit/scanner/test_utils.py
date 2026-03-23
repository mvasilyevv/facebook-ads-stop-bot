from __future__ import annotations

from decimal import Decimal

import pytest

from core.domain import DeliveryStatus
from core.scanner import StatusNormalizer, normalize_delivery_status, parse_scanner_decimal


# Проверяет, что scanner одинаково нормализует русские и английские статусы в доменный enum.
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("Активно", DeliveryStatus.ACTIVE),
        ("ACTIVE", DeliveryStatus.ACTIVE),
        ("На паузе", DeliveryStatus.PAUSED),
        ("Выключено", DeliveryStatus.PAUSED),
        ("Paused", DeliveryStatus.PAUSED),
        ("Обучение", DeliveryStatus.LEARNING),
        ("Learning", DeliveryStatus.LEARNING),
        ("Не показывается", DeliveryStatus.NOT_DELIVERING),
        ("Not delivering", DeliveryStatus.NOT_DELIVERING),
        ("Неизвестно", DeliveryStatus.UNKNOWN),
        (None, DeliveryStatus.UNKNOWN),
    ],
)
def test_status_normalizer_maps_common_statuses(
    raw_status: object | None,
    expected: DeliveryStatus,
) -> None:
    assert normalize_delivery_status(raw_status) == expected
    assert StatusNormalizer.normalize(raw_status) == expected


# Проверяет, что scanner парсит денежные строки и числа в Decimal без привязки к локаторам.
@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("$0.11", Decimal("0.11")),
        ("0,11 $", Decimal("0.11")),
        ("1 234", Decimal("1234")),
        ("1,234.56", Decimal("1234.56")),
        ("1.234,56", Decimal("1234.56")),
        ("($12.50)", Decimal("-12.50")),
        (1234, Decimal("1234")),
        ("—", None),
        ("N/A", None),
        (None, None),
    ],
)
def test_parse_scanner_decimal_handles_money_and_placeholders(
    raw_value: object | None,
    expected: Decimal | None,
) -> None:
    assert parse_scanner_decimal(raw_value) == expected
