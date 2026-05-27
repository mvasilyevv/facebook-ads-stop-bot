# -*- coding: utf-8 -*-
"""Tool request_create_campaign — DRAFT mutation create_campaign.

Принимает минимальный спец-объект: name, objective, special_ad_categories,
daily_budget_usd, status. payload.params = spec_summary с этими полями.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError
from core.meta_api.queue import create_draft_task
from core.meta_api.schemas import MetaMutationPayload

_VALID_OBJECTIVES = frozenset(
    {
        "OUTCOME_LEADS",
        "OUTCOME_SALES",
        "OUTCOME_TRAFFIC",
        "OUTCOME_AWARENESS",
        "OUTCOME_ENGAGEMENT",
        "OUTCOME_APP_PROMOTION",
    }
)


class RequestCreateCampaignTool:
    """DRAFT-задача create_campaign со скелетным spec."""

    name: ClassVar[str] = "request_create_campaign"
    risk_level: ClassVar[RiskLevel] = RiskLevel.DRAFT_REQUIRED
    schema: ClassVar[dict[str, Any]] = {
        "name": "request_create_campaign",
        "description": (
            "Создать DRAFT-задачу на создание новой кампании (create_campaign, через Batch API). "
            "Не исполняется сразу — пользователь подтверждает в TG/TMA. "
            "Все поля кампании передаются в payload.params; реальное создание Этап 5."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string", "description": "act_X"},
                "name": {"type": "string", "minLength": 3, "maxLength": 200},
                "objective": {
                    "type": "string",
                    "enum": sorted(_VALID_OBJECTIVES),
                },
                "daily_budget_usd": {"type": "number", "minimum": 1, "maximum": 100000},
                "lifetime_budget_usd": {"type": "number", "minimum": 1, "maximum": 1000000},
                "status_after_create": {
                    "type": "string",
                    "enum": ["PAUSED", "ACTIVE"],
                    "default": "PAUSED",
                },
                "special_ad_categories": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "NONE",
                            "EMPLOYMENT",
                            "HOUSING",
                            "CREDIT",
                            "ISSUES_ELECTIONS_POLITICS",
                        ],
                    },
                    "default": ["NONE"],
                },
                "reason": {"type": "string", "maxLength": 200},
            },
            "required": ["ad_account_id", "name", "objective"],
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        engine = ctx.require_engine()

        ad_account_id = str(args.get("ad_account_id") or "").strip()
        if not ad_account_id.startswith("act_"):
            raise ToolError("ad_account_id должен начинаться с 'act_'")

        name_ = (args.get("name") or "").strip()
        if not name_ or len(name_) < 3:
            raise ToolError("name обязателен и должен быть ≥3 символов")

        objective = str(args.get("objective") or "").upper()
        if objective not in _VALID_OBJECTIVES:
            raise ToolError(f"objective должен быть одним из: {sorted(_VALID_OBJECTIVES)}")

        daily_raw = args.get("daily_budget_usd")
        lifetime_raw = args.get("lifetime_budget_usd")
        if (daily_raw is None) == (lifetime_raw is None):
            raise ToolError("Передай ровно одно из: daily_budget_usd или lifetime_budget_usd")

        params: dict[str, Any] = {
            "name": name_,
            "objective": objective,
            "status_after_create": (args.get("status_after_create") or "PAUSED").upper(),
            "special_ad_categories": list(args.get("special_ad_categories") or ["NONE"]),
        }
        try:
            if daily_raw is not None:
                daily = Decimal(str(daily_raw))
                if daily <= 0:
                    raise ToolError("daily_budget_usd должен быть > 0")
                params["daily_budget"] = int(daily * 100)
                budget_summary = f"daily=${daily:.2f}"
            else:
                lifetime = Decimal(str(lifetime_raw))
                if lifetime <= 0:
                    raise ToolError("lifetime_budget_usd должен быть > 0")
                params["lifetime_budget"] = int(lifetime * 100)
                budget_summary = f"lifetime=${lifetime:.2f}"
        except (InvalidOperation, ValueError) as exc:
            raise ToolError(f"Неверный формат бюджета: {exc}") from exc

        if params["status_after_create"] not in ("PAUSED", "ACTIVE"):
            raise ToolError("status_after_create: PAUSED или ACTIVE")

        reason = (args.get("reason") or "")[:200]
        if reason:
            params["reason"] = reason

        # target_id="new" — placeholder, фактический campaign_id появится после создания.
        payload = MetaMutationPayload(
            mutation_kind="create_campaign",
            target_id="new",
            params=params,
            ad_account_id=ad_account_id,
        )
        task_id = await create_draft_task(
            engine,
            payload=payload,
            requested_by=ctx.effective_requested_by(),
        )
        if task_id is None:
            raise ToolError("Не удалось создать DRAFT (коллизия idempotency_key?)")

        return (
            f"DRAFT создан: task_id={task_id} (create_campaign, account={ad_account_id}, "
            f"objective={objective}, {budget_summary}, status_after={params['status_after_create']}). "
            f"Имя: «{name_}». Подтверди в TG."
        )
