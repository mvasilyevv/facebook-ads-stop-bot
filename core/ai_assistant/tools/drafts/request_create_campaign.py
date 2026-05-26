# -*- coding: utf-8 -*-
"""Tool request_create_campaign — черновик создания новой кампании из CampaignSpec."""

from __future__ import annotations

from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolError
from core.db import get_session_factory
from core.meta_api.queue import create_mutation_task


class RequestCreateCampaignTool:
    """Создаёт DRAFT mutation_task на создание новой кампании.

    На wave 1 поддерживается только structured spec_summary.
    natural_language_description будет реализован в wave 3 (промпты).
    """

    name: ClassVar[str] = "request_create_campaign"
    risk_level: ClassVar[RiskLevel] = RiskLevel.DRAFT_REQUIRED
    schema: ClassVar[dict[str, Any]] = {
        "name": "request_create_campaign",
        "description": (
            "Создать черновик новой кампании из CampaignSpec. "
            "Поддерживает естественноязычное описание — описание парсится в structured spec."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string"},
                "spec_summary": {
                    "type": "object",
                    "properties": {
                        "offer_code": {"type": "string"},
                        "creo_folder": {"type": "string"},
                        "countries": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 2, "maxLength": 2},
                        },
                        "daily_budget_usd": {"type": "number"},
                        "objective": {
                            "type": "string",
                            "enum": [
                                "OUTCOME_LEADS",
                                "OUTCOME_SALES",
                                "OUTCOME_TRAFFIC",
                                "OUTCOME_AWARENESS",
                            ],
                            "default": "OUTCOME_LEADS",
                        },
                        "attribution_days": {
                            "type": "integer",
                            "enum": [1, 7],
                            "default": 7,
                        },
                    },
                    "required": ["offer_code"],
                },
                "natural_language_description": {
                    "type": "string",
                    "description": (
                        "Альтернатива spec_summary — текст 'создай кампанию по DRC_CR2 на Гваделупу...' "
                        "для AI-парсинга. На wave 1 — не поддерживается."
                    ),
                },
                "reason": {"type": "string"},
            },
            "required": ["ad_account_id"],
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Создаёт DRAFT mutation_task на создание кампании."""
        ad_account_id: str = args["ad_account_id"]
        spec_summary: dict[str, Any] | None = args.get("spec_summary")
        natural_language: str | None = args.get("natural_language_description")
        reason: str = args.get("reason", "")

        # NL-парсер реализуется в wave 3
        if natural_language and not spec_summary:
            raise ToolError(
                "NL parser реализуется в wave 3 (промпты). "
                "Используйте spec_summary для структурированного ввода."
            )

        if not spec_summary:
            raise ToolError(
                "Укажите spec_summary (структурированные параметры кампании) или "
                "дождитесь реализации NL parser в wave 3."
            )

        # Валидация обязательного поля offer_code
        if not spec_summary.get("offer_code"):
            raise ToolError("spec_summary.offer_code обязателен")

        # Нормализация: objective по умолчанию
        if "objective" not in spec_summary:
            spec_summary["objective"] = "OUTCOME_LEADS"
        if "attribution_days" not in spec_summary:
            spec_summary["attribution_days"] = 7

        payload: dict[str, Any] = dict(spec_summary)
        payload["reason"] = reason

        session_factory = get_session_factory()
        async with session_factory() as db:
            task = await create_mutation_task(
                db,
                mutation_kind="create_campaign",
                target_id="",
                ad_account_id=ad_account_id,
                payload=payload,
                requested_by="ai_assistant",
                initial_status="DRAFT",
            )
            await db.commit()

        offer_code = spec_summary["offer_code"]
        objective = spec_summary.get("objective", "OUTCOME_LEADS")
        countries = spec_summary.get("countries", [])
        countries_text = ", ".join(countries) if countries else "не указаны"
        budget = spec_summary.get("daily_budget_usd")
        budget_text = f"{budget:.2f} USD/день" if budget else "не указан"

        return (
            f"Черновик создан.\n"
            f"task_id: {task.id}\n"
            f"mutation_kind: create_campaign\n"
            f"Кабинет: {ad_account_id}\n"
            f"Оффер: {offer_code}\n"
            f"Цель: {objective}\n"
            f"Страны: {countries_text}\n"
            f"Бюджет: {budget_text}\n"
            f"Причина: {reason or '—'}\n"
            f"Подтвердите в Telegram чтобы исполнить."
        )
