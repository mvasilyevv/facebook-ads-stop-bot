from __future__ import annotations

from fastapi import APIRouter

from apps.api.deps import DbSessionDep
from apps.api.schemas.scan_runs import ScanRunItem
from core.repositories import ScanRunsRepository

router = APIRouter(prefix="/scan-runs", tags=["scan-runs"])


@router.get("", response_model=list[ScanRunItem])
async def list_scan_runs(session: DbSessionDep) -> list[ScanRunItem]:
    repo = ScanRunsRepository(session)
    scan_runs = await repo.list_scan_runs()
    return [
        ScanRunItem(
            id=str(scan_run.id),
            browser_host_id=str(scan_run.browser_host_id) if scan_run.browser_host_id else "",
            profile_id=str(scan_run.profile_id) if scan_run.profile_id else "",
            status=scan_run.status.value,
            rows_seen=scan_run.rows_seen,
            rows_parsed=scan_run.rows_parsed,
            scope_summary=scan_run.scope_summary
            if isinstance(scan_run.scope_summary, dict)
            else None,
            error_message=scan_run.error_message,
            started_at=scan_run.started_at,
            finished_at=scan_run.finished_at,
        )
        for scan_run in scan_runs
    ]
