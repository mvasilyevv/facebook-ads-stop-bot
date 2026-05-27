# -*- coding: utf-8 -*-
"""Tool request_clone_campaign — DRAFT mutation duplicate_campaign."""

from __future__ import annotations

from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError
from core.meta_api.queue import create_draft_task
from core.meta_api.schemas import MetaMutationPayload


class RequestCloneCampaignTool:
    """DRAFT-задача на duplicate_campaign.

    deep_copy=true копирует adsets+ads, false — только campaign object.
    new_name либо явно, либо автоматически (исходное_name + ' [clone]').
    """

    name: ClassVar[str] = "request_clone_campaign"
    risk_level: ClassVar[RiskLevel] = RiskLevel.DRAFT_REQUIRED
    schema: ClassVar[dict[str, Any]] = {
        "name": "request_clone_campaign",
        "description": (
            "Создать DRAFT задачу на клонирование кампании (duplicate_campaign). "
            "Не исполняется сразу — нужен confirm в TG. "
            "deep_copy=true — копирует и adsets, и ads."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
                "ad_account_id": {"type": "string", "description": "act_X для аудита"},
                "new_name": {"type": "string", "maxLength": 200},
                "deep_copy": {"type": "boolean", "default": True},
                "status_after_clone": {
                    "type": "string",
                    "enum": ["PAUSED", "ACTIVE"],
                    "default": "PAUSED",
                    "description": "Состояние клона сразу после создания. PAUSED по умолчанию.",
                },
                "reason": {"type": "string", "maxLength": 200},
            },
            "required": ["campaign_id"],
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        engine = ctx.require_engine()

        campaign_id = str(args.get("campaign_id") or "").strip()
        if not campaign_id or not campaign_id.isdigit():
            raise ToolError("campaign_id обязателен и должен быть числовым")

        new_name = (args.get("new_name") or "").strip()
        deep_copy = bool(args.get("deep_copy", True))
        status_after = (args.get("status_after_clone") or "PAUSED").upper()
        if status_after not in ("PAUSED", "ACTIVE"):
            raise ToolError("status_after_clone: PAUSED или ACTIVE")
        ad_account_id = (args.get("ad_account_id") or "").strip() or None
        reason = (args.get("reason") or "")[:200]

        params: dict[str, Any] = {
            "deep_copy": deep_copy,
            "status_after_clone": status_after,
        }
        if new_name:
            params["new_name"] = new_name
        if reason:
            params["reason"] = reason

        payload = MetaMutationPayload(
            mutation_kind="duplicate_campaign",
            target_id=campaign_id,
            params=params,
            ad_account_id=ad_account_id,
        )
        task_id = await create_draft_task(
            engine,
            payload=payload,
            requested_by=ctx.effective_requested_by(),
            created_by_chat_id=ctx.created_by_chat_id,
        )
        if task_id is None:
            raise ToolError("Не удалось создать DRAFT (коллизия idempotency_key?)")

        return (
            f"DRAFT создан: task_id={task_id} (duplicate_campaign, "
            f"src_id={campaign_id}, deep_copy={deep_copy}, after={status_after}). "
            "Подтверди в TG кнопкой ✅ либо отклони ❌."
        )
