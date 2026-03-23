from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.scan_runs import ScanRunItem
from core.repositories import BrowserRepository, ScanRunsRepository

router = APIRouter(prefix="/scan-runs", tags=["scan-runs"])


@router.get("", response_model=list[ScanRunItem])
async def list_scan_runs(
    session: DbSessionDep,
    profile_id: str | None = Query(default=None),
    profile_launch_id: str | None = Query(default=None),
) -> list[ScanRunItem]:
    resolved_profile_id = None
    if profile_id is not None:
        profile = await BrowserRepository(session).get_profile_by_vendor_id(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Профиль `{profile_id}` не найден",
            )
        resolved_profile_id = profile.vendor_profile_id
    repo = ScanRunsRepository(session)
    scan_run_rows = await repo.list_scan_run_rows(
        profile_id=resolved_profile_id,
        profile_launch_id=profile_launch_id,
    )
    return [
        ScanRunItem(
            id=str(scan_run.id),
            browser_host_id=browser_host_name or "",
            profile_id=vendor_profile_id or "",
            profile_launch_id=scan_launch_id,
            profile_launch_name=scan_launch_name,
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
        for scan_run, browser_host_name, vendor_profile_id, scan_launch_id, scan_launch_name in scan_run_rows
    ]
