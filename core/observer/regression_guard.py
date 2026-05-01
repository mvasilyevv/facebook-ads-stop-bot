# -*- coding: utf-8 -*-
"""RegressionGuard: защита от ложных откатов накопительных метрик.

Паттерн аналогичен ZeroScanGuard: блокирует запись при 1–2 подряд
подозрительных сканах, но на 3-й принимает новые значения как базовые.
"""

from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

# Порог подряд идущих регрессий для принятия новых значений как базовых
_REGRESSION_CONFIRM_THRESHOLD = 3


class RegressionGuard:
    """Отслеживает последовательные регрессии накопительных метрик per-объявление.

    При 1–2 подряд подозрительных сканах блокирует запись в историю и UPDATE
    снэпшота. На 3-й подряд регрессии принимает новые значения как новый базовый
    снимок и сбрасывает счётчик. Это предотвращает вечное замораживание снэпшота
    после случайной смены date-preset или не пойманного сброса кабинета.
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        # Идентификаторы объявлений, которые были принудительно приняты в текущем цикле
        self._force_accepted: set[str] = set()

    def should_block(self, fb_ad_id: str, old_snap: object | None, new_data: dict) -> bool:
        """Возвращает True если запись должна быть заблокирована (предполагаемая ложная регрессия).

        Возвращает False в двух случаях:
        - новые значения >= старых (нормальный рост), счётчик сбрасывается;
        - третья подряд регрессия — принимаем новый базовый снимок (force accept).
        Для force-accept id запоминаются в _force_accepted до вызова pop_force_accepted().
        """
        if not _raw_has_regression(old_snap, new_data):
            # Нормальный рост или нет данных для сравнения — сбрасываем счётчик
            self._counters.pop(fb_ad_id, None)
            self._force_accepted.discard(fb_ad_id)
            return False

        count = self._counters.get(fb_ad_id, 0) + 1
        self._counters[fb_ad_id] = count

        if count >= _REGRESSION_CONFIRM_THRESHOLD:
            old_spend = getattr(old_snap, "spend", None)
            new_spend = new_data.get("spend")
            logger.info(
                "Observer: для %s %d цикла подряд накопительные метрики ниже сохранённых — "
                "принимаю новый базовый снимок (старое spend=%s, новое spend=%s)",
                fb_ad_id,
                count,
                old_spend,
                new_spend,
            )
            self._counters[fb_ad_id] = 0
            self._force_accepted.add(fb_ad_id)
            return False

        return True

    def pop_force_accepted(self) -> set[str]:
        """Возвращает и очищает набор fb_ad_id, принятых принудительно в текущем цикле."""
        result = self._force_accepted.copy()
        self._force_accepted.clear()
        return result


def _raw_has_regression(old_snap: object | None, new_data: dict) -> bool:
    """Чистая проверка регрессии без сайд-эффектов (для переиспользования в тестах)."""
    if old_snap is None:
        return False
    # Отложенный импорт во избежание циклических зависимостей
    from core.observer.snapshot_writer import _CUMULATIVE_METRICS  # noqa: PLC0415

    for key in _CUMULATIVE_METRICS:
        old_val = getattr(old_snap, key, None)
        new_val = new_data.get(key)
        if old_val is None or new_val is None:
            continue
        if Decimal(str(new_val)) < Decimal(str(old_val)):
            return True
    return False
