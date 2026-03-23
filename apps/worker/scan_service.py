from __future__ import annotations

from dataclasses import replace
from typing import Any

from apps.worker.full_scan_service import FullScanService, WorkerScanResult
from apps.worker.pipeline_support import restore_scope_from_existing_ad


class WorkerScanService(FullScanService):
    """Совместимое имя для сервиса полного скана в новой архитектуре."""

    def _restore_scope_from_existing_ad(self, *, row, existing_ad: Any | None):
        return restore_scope_from_existing_ad(row=row, existing_ad=existing_ad)

    @staticmethod
    def _restore_metrics_from_latest_snapshot(*, row, latest_snapshot: Any | None):
        if latest_snapshot is None:
            return row
        if not WorkerScanService._row_needs_snapshot_metric_fallback(row):
            return row
        return replace(
            row,
            spend=latest_snapshot.spend or row.spend,
            clicks=latest_snapshot.clicks or row.clicks,
            cpc=latest_snapshot.cpc if latest_snapshot.cpc is not None else row.cpc,
            leads=latest_snapshot.leads or row.leads,
            cost_per_lead=(
                latest_snapshot.cost_per_lead
                if latest_snapshot.cost_per_lead is not None
                else row.cost_per_lead
            ),
            registrations=latest_snapshot.registrations or row.registrations,
            cost_per_registration=(
                latest_snapshot.cost_per_registration
                if latest_snapshot.cost_per_registration is not None
                else row.cost_per_registration
            ),
            deposits=latest_snapshot.deposits or row.deposits,
        )

    @staticmethod
    def _row_needs_snapshot_metric_fallback(row) -> bool:
        return (
            row.spend.is_zero()
            and row.clicks == 0
            and row.cpc is None
            and row.leads == 0
            and row.cost_per_lead is None
            and row.registrations == 0
            and row.cost_per_registration is None
            and row.deposits == 0
        )


__all__ = [
    "FullScanService",
    "WorkerScanResult",
    "WorkerScanService",
]
