from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.common import ExecutionState
from apps.api.schemas.decisions import DecisionItem
from core.domain import DecisionType
from core.repositories import BrowserRepository, DecisionsRepository

router = APIRouter(prefix="/decisions", tags=["decisions"])


def _resolve_execution_state(decision) -> ExecutionState:
    raw_status = (decision.action_status or "").strip().upper()
    if raw_status in {item.value for item in ExecutionState}:
        return ExecutionState(raw_status)
    if decision.action_executed:
        return ExecutionState.SUCCEEDED
    if decision.decision == DecisionType.NO_ACTION:
        return ExecutionState.NOT_REQUIRED
    if decision.decision in {DecisionType.WOULD_PAUSE, DecisionType.WOULD_RESUME}:
        return ExecutionState.SKIPPED_BY_MODE
    return ExecutionState.NOT_REQUIRED


@router.get("", response_model=list[DecisionItem])
async def list_decisions(
    session: DbSessionDep,
    profile_id: str | None = Query(default=None),
    profile_launch_id: str | None = Query(default=None),
) -> list[DecisionItem]:
    resolved_profile_id = None
    if profile_id is not None:
        profile = await BrowserRepository(session).get_profile_by_vendor_id(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Профиль `{profile_id}` не найден",
            )
        resolved_profile_id = profile.id
    repo = DecisionsRepository(session)
    decisions = await repo.list_decisions(
        profile_id=resolved_profile_id,
        profile_launch_id=profile_launch_id,
    )
    return [
        DecisionItem(
            id=str(decision.id),
            scan_run_id=str(decision.scan_run_id),
            fb_ad_id=decision.fb_ad_id,
            rule_id=str(decision.rule_id) if decision.rule_id is not None else None,
            decision=decision.decision.value,
            reason=decision.reason,
            action_executed=decision.action_executed,
            action_status=decision.action_status,
            execution_state=_resolve_execution_state(decision),
            resolved_cpa_usd=decision.resolved_cpa_usd,
            created_at=decision.created_at,
        )
        for decision in decisions
    ]
