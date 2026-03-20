from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from apps.api.deps import ApiStateDep
from apps.api.schemas.rules import RuleItem, RuleUpdateRequest

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleItem])
async def list_rules(api_state: ApiStateDep) -> list[RuleItem]:
    return api_state.rules_service.list_rules()


@router.put("/{rule_id}", response_model=RuleItem)
async def update_rule(rule_id: str, payload: RuleUpdateRequest, api_state: ApiStateDep) -> RuleItem:
    rule = api_state.rules_service.update_rule(rule_id, payload)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило не найдено")
    return rule
