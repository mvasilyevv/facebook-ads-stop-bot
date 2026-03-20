from __future__ import annotations

from fastapi import APIRouter

from apps.api.deps import ApiStateDep
from apps.api.schemas.scan_runs import ScanRunItem

router = APIRouter(prefix="/scan-runs", tags=["scan-runs"])


@router.get("", response_model=list[ScanRunItem])
async def list_scan_runs(api_state: ApiStateDep) -> list[ScanRunItem]:
    return api_state.scan_runs_service.list_scan_runs()
