# -*- coding: utf-8 -*-
"""Чистая функция-классификатор исхода скан-цикла observer'а.

Принимает ScanResult от browser-agent + контекст истории по fb_ad_id,
возвращает одно из 7 финальных состояний цикла.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from clients.python_grpc.client import ScanResult


class ScanOutcome(Enum):
    """Финальное состояние цикла observer'а."""

    OK = "OK"
    OK_PARTIAL = "OK_PARTIAL"
    EMPTY_OK = "EMPTY_OK"
    EMPTY_BAD = "EMPTY_BAD"
    STALE_DATA = "STALE_DATA"
    BROWSER_LOST = "BROWSER_LOST"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True)
class ScanOutcomeDetails:
    """Результат классификации + детали для записи в scan_runs."""

    kind: ScanOutcome
    partial_count: int = 0
    empty_reason: str | None = None
    stale_ratio: float = 0.0
    note: str = ""


def classify_scan_outcome(
    result: ScanResult,
    *,
    stale_threshold: float,
    has_history_for_ids: Callable[[list[str]], bool],
) -> ScanOutcomeDetails:
    """Классифицировать результат сканирования.

    Args:
        result: ScanResult от browser-agent.
        stale_threshold: доля строк-«прочерков» (0..1), после которой считаем STALE_DATA.
        has_history_for_ids: предикат — у этих fb_ad_id когда-то были непустые метрики.
            Используется как гард: если у текущих объявлений никогда не было данных,
            то отсутствие метрик — норма, а не STALE_DATA.
    """
    row_count = len(result.rows)

    if row_count == 0:
        reason = result.empty_reason
        if reason in {"no_active_ads", "filter_excludes_all"}:
            return ScanOutcomeDetails(kind=ScanOutcome.EMPTY_OK, empty_reason=reason)
        return ScanOutcomeDetails(
            kind=ScanOutcome.EMPTY_BAD,
            empty_reason=reason or "table_not_found",
        )

    stale_ratio = result.rows_with_all_metrics_empty / row_count
    if stale_ratio >= stale_threshold:
        ad_ids = [
            getattr(row, "fb_ad_id", "") for row in result.rows if getattr(row, "fb_ad_id", "")
        ]
        if ad_ids and has_history_for_ids(ad_ids):
            return ScanOutcomeDetails(
                kind=ScanOutcome.STALE_DATA,
                stale_ratio=stale_ratio,
                note=f"{result.rows_with_all_metrics_empty}/{row_count} строк без метрик",
            )

    if result.partial_row_ids:
        return ScanOutcomeDetails(
            kind=ScanOutcome.OK_PARTIAL,
            partial_count=len(result.partial_row_ids),
            stale_ratio=stale_ratio,
        )

    return ScanOutcomeDetails(kind=ScanOutcome.OK, stale_ratio=stale_ratio)
