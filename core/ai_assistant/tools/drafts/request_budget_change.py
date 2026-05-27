# -*- coding: utf-8 -*-
"""Tool request_budget_change — DRAFT mutation set_adset_budget."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError
from core.meta_api.queue import create_draft_task
from core.meta_api.schemas import MetaMutationPayload


class RequestBudgetChangeTool:
    """Создать DRAFT-задачу set_adset_budget.

    Принимает либо daily_budget_usd, либо lifetime_budget_usd (взаимоисключающие).
    Бюджет передаётся в центах (Meta API формат) в payload.params.
    """

    name: ClassVar[str] = "request_budget_change"
    risk_level: ClassVar[RiskLevel] = RiskLevel.DRAFT_REQUIRED
    schema: ClassVar[dict[str, Any]] = {
        "name": "request_budget_change",
        "description": (
            "Создать DRAFT-задачу на изменение бюджета adset (set_adset_budget). "
            "Задача попадает в task_queue со status=draft и НЕ исполняется — "
            "пользователь подтверждает в TG/TMA inline-кнопкой. "
            "Передавай ровно одно поле: daily_budget_usd ИЛИ lifetime_budget_usd."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "adset_id": {"type": "string", "description": "Marketing API adset_id"},
                "ad_account_id": {"type": "string", "description": "act_X (для аудита)"},
                "daily_budget_usd": {"type": "number", "minimum": 0.5, "maximum": 100000},
                "lifetime_budget_usd": {"type": "number", "minimum": 1, "maximum": 1000000},
                "reason": {
                    "type": "string",
                    "description": "Краткое обоснование для логов и TG (1-200 символов)",
                },
            },
            "required": ["adset_id"],
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        engine = ctx.require_engine()

        adset_id = str(args.get("adset_id") or "").strip()
        if not adset_id or not adset_id.isdigit():
            raise ToolError("adset_id обязателен и должен быть числовым")
        ad_account_id = (args.get("ad_account_id") or "").strip() or None

        daily_raw = args.get("daily_budget_usd")
        lifetime_raw = args.get("lifetime_budget_usd")
        if (daily_raw is None) == (lifetime_raw is None):
            raise ToolError("Передай ровно одно из: daily_budget_usd или lifetime_budget_usd")

        params: dict[str, Any] = {}
        try:
            if daily_raw is not None:
                daily = Decimal(str(daily_raw))
                if daily <= 0:
                    raise ToolError("daily_budget_usd должен быть > 0")
                params["daily_budget"] = int(daily * 100)  # cents для Meta
                budget_summary = f"daily=${daily:.2f}"
            else:
                lifetime = Decimal(str(lifetime_raw))
                if lifetime <= 0:
                    raise ToolError("lifetime_budget_usd должен быть > 0")
                params["lifetime_budget"] = int(lifetime * 100)
                budget_summary = f"lifetime=${lifetime:.2f}"
        except (InvalidOperation, ValueError) as exc:
            raise ToolError(f"Неверный формат бюджета: {exc}") from exc

        reason = (args.get("reason") or "")[:200]
        if reason:
            params["reason"] = reason

        payload = MetaMutationPayload(
            mutation_kind="set_adset_budget",
            target_id=adset_id,
            params=params,
            ad_account_id=ad_account_id,
        )
        task_id = await create_draft_task(
            engine,
            payload=payload,
            requested_by=ctx.effective_requested_by(),
        )
        if task_id is None:
            raise ToolError("Не удалось создать DRAFT — возможна коллизия idempotency_key")

        return (
            f"DRAFT создан: task_id={task_id} (mutation=set_adset_budget, "
            f"adset_id={adset_id}, {budget_summary}). "
            "Подтверди в TG кнопкой ✅ либо отклони ❌. "
            "DRAFT автоматически отменится через 24 часа без подтверждения."
        )
