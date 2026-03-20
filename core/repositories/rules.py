from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.operations import Rule, RuleSet
from core.repositories.base import AsyncRepository


@dataclass(slots=True, frozen=True)
class DefaultRuleSpec:
    code: str
    name: str
    description: str
    priority: int
    cpa_multiplier: Decimal | None = None


DEFAULT_RULE_SET_CODE = "default-stop-rules"
DEFAULT_RULE_SET_NAME = "Базовые стоп-правила"
DEFAULT_RULES: tuple[DefaultRuleSpec, ...] = (
    DefaultRuleSpec(
        code="stop_high_cpc",
        name="Стоп по дорогому клику",
        description="Останавливает объявление, если стоимость клика превысила 2% CPA.",
        priority=10,
        cpa_multiplier=Decimal("0.02"),
    ),
    DefaultRuleSpec(
        code="stop_high_cpl",
        name="Стоп по дорогому лиду",
        description="Останавливает объявление, если стоимость лида превысила 10% CPA.",
        priority=20,
        cpa_multiplier=Decimal("0.10"),
    ),
    DefaultRuleSpec(
        code="stop_high_cpr",
        name="Стоп по дорогой регистрации",
        description="Останавливает объявление, если стоимость регистрации превысила 20% CPA.",
        priority=30,
        cpa_multiplier=Decimal("0.20"),
    ),
    DefaultRuleSpec(
        code="stop_five_regs_without_deposit",
        name="Стоп после пяти регистраций без депозита",
        description="Останавливает объявление, если накопилось 5 регистраций и не было депозитов.",
        priority=40,
    ),
    DefaultRuleSpec(
        code="stop_spend_window_without_deposit",
        name="Стоп по расходу без депозита",
        description="Останавливает объявление, если расход дошел до 50% CPA, депозитов нет, а регистрация в норме.",
        priority=50,
        cpa_multiplier=Decimal("0.50"),
    ),
    DefaultRuleSpec(
        code="stop_spend_after_deposit",
        name="Стоп после первого депозита по расходу",
        description="Останавливает объявление, если при наличии депозита расход превысил 70% CPA.",
        priority=60,
        cpa_multiplier=Decimal("0.70"),
    ),
)


class RulesRepository(AsyncRepository):
    """Репозиторий rule set и правил стоп-логики."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_rule_set_by_code(self, code: str) -> RuleSet | None:
        result = await self.session.scalars(select(RuleSet).where(RuleSet.code == code))
        return result.first()

    async def create_rule_set(
        self,
        *,
        code: str,
        name: str,
        is_active: bool = True,
        config_json: dict | None = None,
    ) -> RuleSet:
        rule_set = RuleSet(
            code=code,
            name=name,
            is_active=is_active,
            config_json=config_json or {},
        )
        self.session.add(rule_set)
        await self.session.flush()
        return rule_set

    async def list_rules(self) -> list[Rule]:
        result = await self.session.scalars(select(Rule))
        rules = list(result.all())
        return sorted(rules, key=lambda item: int(item.config_json.get("priority", 100)))

    async def get_rule(self, rule_id: str) -> Rule | None:
        return await self.session.get(Rule, UUID(str(rule_id)))

    async def get_rule_by_code(self, code: str) -> Rule | None:
        result = await self.session.scalars(select(Rule).where(Rule.code == code))
        return result.first()

    async def upsert_rule(
        self,
        *,
        rule_set_id: str,
        code: str,
        name: str,
        description: str,
        is_enabled: bool,
        priority: int,
        cpa_multiplier: Decimal | None = None,
    ) -> Rule:
        rule = await self.get_rule_by_code(code)
        config_json = {"priority": priority}
        if cpa_multiplier is not None:
            config_json["cpa_multiplier"] = str(cpa_multiplier)
        if rule is None:
            rule = Rule(
                rule_set_id=rule_set_id,
                code=code,
                name=name,
                description=description,
                is_enabled=is_enabled,
                config_json=config_json,
            )
            self.session.add(rule)
        else:
            rule.rule_set_id = rule_set_id
            rule.name = name
            rule.description = description
            rule.is_enabled = is_enabled
            rule.config_json = config_json
        await self.session.flush()
        return rule

    async def update_rule(
        self,
        rule_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        is_enabled: bool | None = None,
        priority: int | None = None,
        cpa_multiplier: Decimal | None = None,
    ) -> Rule | None:
        rule = await self.get_rule(rule_id)
        if rule is None:
            return None
        if name is not None:
            rule.name = name
        if description is not None:
            rule.description = description
        if is_enabled is not None:
            rule.is_enabled = is_enabled
        updated_config = dict(rule.config_json)
        if priority is not None:
            updated_config["priority"] = priority
        if cpa_multiplier is not None:
            updated_config["cpa_multiplier"] = str(cpa_multiplier)
        rule.config_json = updated_config
        rule.updated_at = datetime.now(tz=UTC)
        await self.session.flush()
        return rule

    async def ensure_default_rules(self) -> None:
        rule_set = await self.get_rule_set_by_code(DEFAULT_RULE_SET_CODE)
        if rule_set is None:
            rule_set = await self.create_rule_set(
                code=DEFAULT_RULE_SET_CODE,
                name=DEFAULT_RULE_SET_NAME,
                is_active=True,
            )

        for default_rule in DEFAULT_RULES:
            existing_rule = await self.get_rule_by_code(default_rule.code)
            if existing_rule is None:
                await self.upsert_rule(
                    rule_set_id=rule_set.id,
                    code=default_rule.code,
                    name=default_rule.name,
                    description=default_rule.description,
                    is_enabled=True,
                    priority=default_rule.priority,
                    cpa_multiplier=default_rule.cpa_multiplier,
                )
