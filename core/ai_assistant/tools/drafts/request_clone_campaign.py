# -*- coding: utf-8 -*-
"""Tool request_clone_campaign — черновик клонирования кампании."""

from __future__ import annotations

from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel
from core.db import get_session_factory
from core.meta_api.queue import create_mutation_task


class RequestCloneCampaignTool:
    """Создаёт DRAFT mutation_task на клонирование кампании.

    POST /{campaign_id}/copies через Marketing API.
    deep_copy=True клонирует всю иерархию: кампания + адсеты + объявления.
    """

    name: ClassVar[str] = "request_clone_campaign"
    risk_level: ClassVar[RiskLevel] = RiskLevel.DRAFT_REQUIRED
    schema: ClassVar[dict[str, Any]] = {
        "name": "request_clone_campaign",
        "description": (
            "Создать черновик клонирования кампании. "
            "POST /{campaign_id}/copies?deep_copy=true. "
            "Пользователь подтверждает в Telegram."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string"},
                "source_campaign_id": {"type": "string"},
                "target_name": {
                    "type": "string",
                    "description": "Новое имя клона (опционально, иначе Meta добавит -Copy)",
                },
                "deep_copy": {
                    "type": "boolean",
                    "default": True,
                    "description": "True = клонировать всё (adsets + ads); False = только campaign",
                },
                "rename_options": {
                    "type": "object",
                    "description": "Опции переименования adsets/ads (опционально)",
                },
                "reason": {"type": "string"},
            },
            "required": ["ad_account_id", "source_campaign_id"],
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Создаёт DRAFT mutation_task на клонирование кампании."""
        ad_account_id: str = args["ad_account_id"]
        source_campaign_id: str = args["source_campaign_id"]
        target_name: str | None = args.get("target_name")
        deep_copy: bool = args.get("deep_copy", True)
        rename_options: dict[str, Any] | None = args.get("rename_options")
        reason: str = args.get("reason", "")

        # Формируем payload со всеми параметрами клонирования
        payload: dict[str, Any] = {
            "source_campaign_id": source_campaign_id,
            "deep_copy": deep_copy,
            "reason": reason,
        }
        if target_name:
            payload["target_name"] = target_name
        if rename_options:
            payload["rename_options"] = rename_options

        session_factory = get_session_factory()
        async with session_factory() as db:
            task = await create_mutation_task(
                db,
                mutation_kind="clone_campaign",
                target_id=source_campaign_id,
                ad_account_id=ad_account_id,
                payload=payload,
                requested_by="ai_assistant",
                initial_status="DRAFT",
            )
            await db.commit()

        clone_desc = f"→ '{target_name}'" if target_name else "→ Meta добавит -Copy"
        depth_desc = "полный клон (adsets + ads)" if deep_copy else "только кампания"

        return (
            f"Черновик создан.\n"
            f"task_id: {task.id}\n"
            f"mutation_kind: clone_campaign\n"
            f"Кампания: {source_campaign_id} (кабинет {ad_account_id}) {clone_desc}\n"
            f"Глубина: {depth_desc}\n"
            f"Причина: {reason or '—'}\n"
            f"Подтвердите в Telegram чтобы исполнить."
        )
