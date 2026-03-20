from __future__ import annotations

from datetime import UTC, datetime

from apps.api.schemas.rules import RuleItem, RuleUpdateRequest
from apps.api.services.state import ApiStore


class RulesService:
    def __init__(self, store: ApiStore) -> None:
        self._store = store

    def list_rules(self) -> list[RuleItem]:
        return list(self._store.rules.values())

    def update_rule(self, rule_id: str, payload: RuleUpdateRequest) -> RuleItem | None:
        rule = self._store.rules.get(rule_id)
        if rule is None:
            return None
        updated_rule = rule.model_copy(
            update={
                key: value
                for key, value in payload.model_dump(exclude_unset=True).items()
                if value is not None
            }
        )
        updated_rule = updated_rule.model_copy(update={"updated_at": datetime.now(tz=UTC)})
        self._store.rules[rule_id] = updated_rule
        return updated_rule
