# -*- coding: utf-8 -*-
"""ZeroScanGuard: инкапсулирует логику пропуска подозрительных батчей снэпшотов.

Защищает от затирания живого среза в случае временного сбоя парсинга
или временно неполного ответа Facebook. Любой пустой или подозрительно
урезанный батч требует подтверждения на следующем цикле.

Логика смены суток кабинета вынесена в endpoint
POST /api/observer/start-new-cabinet-day и больше не триггерится guard'ом.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import Enum

logger = logging.getLogger(__name__)

_SUSPICIOUS_PARTIAL_BATCH_DROP_RATIO = 0.85
_SUSPICIOUS_PARTIAL_BATCH_MIN_DROP = 5


class GuardSkipReason(str, Enum):
    """Причина, по которой guard просит пропустить батч."""

    ZERO_SCAN_PENDING = "guard_pending_zero"
    PARTIAL_BATCH_PENDING = "guard_pending_partial"


def _is_zero_batch(snapshot_data: list[dict]) -> bool:
    """True, если в батче все ключевые метрики у всех записей равны нулю/None."""
    metrics_to_check = (
        "spend",
        "clicks",
        "leads",
        "registrations",
        "deposits",
    )
    for item in snapshot_data:
        for metric in metrics_to_check:
            value = item.get(metric)
            if value in (None, 0, "0", "0.0", "0.00"):
                continue
            try:
                if float(value) != 0.0:
                    return False
            except (TypeError, ValueError):
                continue
    return True


class ZeroScanGuard:
    """Отслеживает подозрительные zero-scan и partial-batch сигналы.

    Возвращает GuardSkipReason | None из should_skip:
        - ZERO_SCAN_PENDING — первый полный zero-batch, ждём подтверждения.
        - PARTIAL_BATCH_PENDING — первый подозрительно урезанный батч.
        - None — батч принят (сохраняется).
    """

    def __init__(self) -> None:
        self._pending_zero_scan_at: datetime | None = None
        self._pending_partial_batch_at: datetime | None = None
        self._last_accepted_size: int | None = None

    def initialize_from_count(self, count: int) -> None:
        """Восстанавливает базовый размер батча из БД при старте воркера."""
        if self._last_accepted_size is None and count > 0:
            self._last_accepted_size = count
            logger.info(
                "ZeroScanGuard: базовый размер батча восстановлен из БД: %s снэпшотов",
                count,
            )

    def should_skip(self, snapshot_data: list[dict]) -> GuardSkipReason | None:
        """Возвращает причину skip или None если батч можно сохранять."""
        if not snapshot_data:
            self._pending_zero_scan_at = None
            self._pending_partial_batch_at = None
            return None

        scan_started_at = max(
            (
                item.get("last_observed_at")
                for item in snapshot_data
                if item.get("last_observed_at")
            ),
            default=datetime.now(UTC),
        )
        snapshot_count = len(snapshot_data)

        if _is_zero_batch(snapshot_data):
            if self._pending_zero_scan_at is None:
                self._pending_zero_scan_at = scan_started_at
                logger.warning(
                    "Observer: получен полный zero-batch без подтверждения — "
                    "пропускаю сохранение до следующего цикла"
                )
                return GuardSkipReason.ZERO_SCAN_PENDING
            logger.warning("Observer: повторный zero-batch подтверждён — принимаю нулевой срез")
            self._pending_zero_scan_at = None
            self._last_accepted_size = snapshot_count
            return None

        if self._pending_zero_scan_at is not None:
            logger.warning(
                "Observer: zero-batch не подтвердился на следующем цикле, "
                "продолжаю работать по живому срезу"
            )
        self._pending_zero_scan_at = None

        previous_size = self._last_accepted_size
        suspicious_partial = (
            previous_size is not None
            and previous_size - snapshot_count >= _SUSPICIOUS_PARTIAL_BATCH_MIN_DROP
            and snapshot_count < previous_size * _SUSPICIOUS_PARTIAL_BATCH_DROP_RATIO
        )
        if suspicious_partial:
            if self._pending_partial_batch_at is None:
                self._pending_partial_batch_at = scan_started_at
                logger.warning(
                    "Observer: подозрительно неполный батч (%s вместо %s) — "
                    "пропускаю сохранение до подтверждения",
                    snapshot_count,
                    previous_size,
                )
                return GuardSkipReason.PARTIAL_BATCH_PENDING
            logger.warning(
                "Observer: повторный неполный батч подтверждён (%s вместо %s) — "
                "принимаю урезанный срез",
                snapshot_count,
                previous_size,
            )
            self._pending_partial_batch_at = None
            self._last_accepted_size = snapshot_count
            return None

        if self._pending_partial_batch_at is not None:
            logger.warning("Observer: неполный батч не подтвердился, сохраняю восстановленный срез")
        self._pending_partial_batch_at = None
        self._last_accepted_size = snapshot_count
        return None
