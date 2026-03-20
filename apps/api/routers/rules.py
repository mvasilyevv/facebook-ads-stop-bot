from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.rules import RuleItem, RuleUpdateRequest
from core.repositories import RulesRepository

router = APIRouter(prefix="/rules", tags=["rules"])


def _parse_cpa_multiplier(raw_value: object) -> Decimal | None:
    if raw_value is None:
        return None
    return Decimal(str(raw_value))


def _map_rule_item(rule) -> RuleItem:
    return RuleItem(
        id=str(rule.id),
        code=rule.code,
        title=rule.name,
        description=rule.description,
        is_enabled=rule.is_enabled,
        priority=int(rule.config_json.get("priority", 100)),
        cpa_multiplier=_parse_cpa_multiplier(rule.config_json.get("cpa_multiplier")),
        updated_at=rule.updated_at,
    )


@router.get("", response_model=list[RuleItem])
async def list_rules(session: DbSessionDep) -> list[RuleItem]:
    repo = RulesRepository(session)
    rules = await repo.list_rules()
    return [_map_rule_item(rule) for rule in rules]


@router.put("/{rule_id}", response_model=RuleItem)
async def update_rule(rule_id: str, payload: RuleUpdateRequest, session: DbSessionDep) -> RuleItem:
    repo = RulesRepository(session)
    rule = await repo.update_rule(
        rule_id,
        name=payload.title,
        description=payload.description,
        is_enabled=payload.is_enabled,
        priority=payload.priority,
        cpa_multiplier=payload.cpa_multiplier,
    )
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило не найдено")
    await session.commit()
    return _map_rule_item(rule)
