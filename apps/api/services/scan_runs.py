from __future__ import annotations

from apps.api.schemas.scan_runs import ScanRunItem
from apps.api.services.state import ApiStore


class ScanRunsService:
    def __init__(self, store: ApiStore) -> None:
        self._store = store

    def list_scan_runs(self) -> list[ScanRunItem]:
        return list(self._store.scan_runs)
