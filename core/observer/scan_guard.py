# -*- coding: utf-8 -*-
"""ZeroScanGuard: инкапсулирует логику пропуска подозрительных батчей снэпшотов."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from core.cabinet_day import is_cabinet_day_reset_scan

logger = logging.getLogger(__name__)

_SUSPICIOUS_PARTIAL_BATCH_DROP_RATIO = 0.85
_SUSPICIOUS_PARTIAL_BATCH_MIN_DROP = 5


class ZeroScanGuard:
    """Отслеживает подозрительные zero-scan и partial-batch сигналы.

    Предотвращает затирание актуального снимка в случае временного
    сбоя парсинга или reset-кабинета без второго подтверждения.
    """

    def __init__(self) -> None:
        self._pending_zero_scan_at: datetime | None = None
        self._pending_partial_batch_at: datetime | None = None
        self._last_accepted_size: int | None = None

    def initialize_from_count(self, count: int) -> None:
        """Инициализирует базовый размер батча из БД при старте воркера.

        Вызывается один раз перед первым циклом сканирования, чтобы защита
        от частичных батчей работала с первого же цикла после перезапуска.
        """
        if self._last_accepted_size is None and count > 0:
            self._last_accepted_size = count
            logger.info(
                "ZeroScanGuard: базовый размер батча восстановлен из БД: %s снэпшотов", count
            )

    def should_skip(self, snapshot_data: list[dict]) -> bool:
        """Возвращает True если батч подозрителен и должен быть пропущен."""
        if not snapshot_data:
            self._pending_zero_scan_at = None
            self._pending_partial_batch_at = None
            return False

        scan_started_at = max(
            (
                item.get("last_observed_at")
                for item in snapshot_data
                if item.get("last_observed_at")
            ),
            default=datetime.now(UTC),
        )
        snapshot_count = len(snapshot_data)

        if not is_cabinet_day_reset_scan(snapshot_data):
            if self._pending_zero_scan_at is not None:
                logger.warning(
                    "Observer: zero-scan не подтвердился на следующем цикле, "
                    "продолжаю работать по прежнему живому срезу"
                )
            self._pending_zero_scan_at = None

            previous_snapshot_count = self._last_accepted_size
            suspicious_partial_batch = (
                previous_snapshot_count is not None
                and previous_snapshot_count - snapshot_count >= _SUSPICIOUS_PARTIAL_BATCH_MIN_DROP
                and snapshot_count < previous_snapshot_count * _SUSPICIOUS_PARTIAL_BATCH_DROP_RATIO
            )
            if suspicious_partial_batch:
                if self._pending_partial_batch_at is None:
                    self._pending_partial_batch_at = scan_started_at
                    logger.warning(
                        "Observer: получен подозрительно неполный батч (%s вместо %s) — "
                        "пропускаю сохранение до повторного подтверждения",
                        snapshot_count,
                        previous_snapshot_count,
                    )
                    return True

                logger.warning(
                    "Observer: повторный неполный батч подтверждён (%s вместо %s) — "
                    "принимаю новый урезанный срез",
                    snapshot_count,
                    previous_snapshot_count,
                )
                self._pending_partial_batch_at = None
                self._last_accepted_size = snapshot_count
                return False

            if self._pending_partial_batch_at is not None:
                logger.warning(
                    "Observer: неполный батч не подтвердился на следующем цикле, "
                    "сохраняю только восстановленный полный срез"
                )
            self._pending_partial_batch_at = None
            self._last_accepted_size = snapshot_count
            return False

        self._pending_partial_batch_at = None
        if self._pending_zero_scan_at is None:
            self._pending_zero_scan_at = scan_started_at
            logger.warning(
                "Observer: получен полный zero-scan без подтверждения — "
                "пропускаю сохранение текущего батча до следующего цикла"
            )
            return True

        logger.warning(
            "Observer: повторный zero-scan подтверждён — принимаю новый нулевой срез кабинета"
        )
        self._pending_zero_scan_at = None
        self._last_accepted_size = snapshot_count
        return False
