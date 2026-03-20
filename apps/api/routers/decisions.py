from __future__ import annotations

from fastapi import APIRouter

from apps.api.deps import DbSessionDep
from apps.api.schemas.decisions import DecisionItem
from core.repositories import DecisionsRepository

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.get("", response_model=list[DecisionItem])
async def list_decisions(session: DbSessionDep) -> list[DecisionItem]:
    repo = DecisionsRepository(session)
    decisions = await repo.list_decisions()
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
            resolved_cpa_usd=decision.resolved_cpa_usd,
            created_at=decision.created_at,
        )
        for decision in decisions
    ]
