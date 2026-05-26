# -*- coding: utf-8 -*-
"""Tool request_budget_change — черновик изменения бюджета объявления или ad set."""

from __future__ import annotations

from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolError
from core.db import get_session_factory
from core.meta_api.queue import create_mutation_task


class RequestBudgetChangeTool:
    """Создаёт DRAFT mutation_task на изменение бюджета.

    Поддерживает daily_budget_usd либо lifetime_budget_usd (не оба).
    Конвертирует USD → центы перед записью в payload.
    """

    name: ClassVar[str] = "request_budget_change"
    risk_level: ClassVar[RiskLevel] = RiskLevel.DRAFT_REQUIRED
    schema: ClassVar[dict[str, Any]] = {
        "name": "request_budget_change",
        "description": (
            "Создать черновик изменения бюджета объявления или ad set. "
            "Изменение НЕ применяется сразу — пользователь подтверждает в Telegram."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string"},
                "entity_id": {
                    "type": "string",
                    "description": "ad_id или adset_id",
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["ad", "adset"],
                    "default": "adset",
                },
                "daily_budget_usd": {
                    "type": "number",
                    "description": "Новый дневной бюджет в долларах",
                },
                "lifetime_budget_usd": {
                    "type": "number",
                    "description": "Альтернатива daily — бюджет на весь период",
                },
                "reason": {
                    "type": "string",
                    "description": "Зачем меняем (для аудита)",
                },
            },
            "required": ["ad_account_id", "entity_id"],
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Создаёт DRAFT mutation_task на изменение бюджета."""
        ad_account_id: str = args["ad_account_id"]
        entity_id: str = args["entity_id"]
        entity_type: str = args.get("entity_type", "adset")
        daily_budget_usd: float | None = args.get("daily_budget_usd")
        lifetime_budget_usd: float | None = args.get("lifetime_budget_usd")
        reason: str = args.get("reason", "")

        # Валидация: ровно один тип бюджета должен быть указан
        if daily_budget_usd is None and lifetime_budget_usd is None:
            raise ToolError("Укажите один из параметров: daily_budget_usd или lifetime_budget_usd")
        if daily_budget_usd is not None and lifetime_budget_usd is not None:
            raise ToolError(
                "Укажите только один параметр: daily_budget_usd или lifetime_budget_usd, не оба"
            )

        # Конвертация USD → центы (умножение на 100, округление)
        payload: dict[str, Any] = {
            "entity_type": entity_type,
            "reason": reason,
        }
        if daily_budget_usd is not None:
            payload["daily_budget_cents"] = round(daily_budget_usd * 100)
        if lifetime_budget_usd is not None:
            payload["lifetime_budget_cents"] = round(lifetime_budget_usd * 100)

        # Создаём DRAFT-задачу через фабрику сессий
        session_factory = get_session_factory()
        async with session_factory() as db:
            task = await create_mutation_task(
                db,
                mutation_kind="set_budget",
                target_id=entity_id,
                ad_account_id=ad_account_id,
                payload=payload,
                requested_by="ai_assistant",
                initial_status="DRAFT",
            )
            await db.commit()

        # Формируем удобочитаемое описание изменения
        if daily_budget_usd is not None:
            budget_desc = f"дневной бюджет {daily_budget_usd:.2f} USD ({payload['daily_budget_cents']} центов)"
        else:
            budget_desc = f"бюджет на период {lifetime_budget_usd:.2f} USD ({payload['lifetime_budget_cents']} центов)"

        return (
            f"Черновик создан.\n"
            f"task_id: {task.id}\n"
            f"mutation_kind: set_budget\n"
            f"Объект: {entity_type} {entity_id} (кабинет {ad_account_id})\n"
            f"Изменение: {budget_desc}\n"
            f"Причина: {reason or '—'}\n"
            f"Подтвердите в Telegram чтобы исполнить."
        )
