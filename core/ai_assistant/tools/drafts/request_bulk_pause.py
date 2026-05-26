# -*- coding: utf-8 -*-
"""Tool request_bulk_pause — черновик пакетной остановки объявлений по фильтру."""

from __future__ import annotations

from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolError
from core.db import get_session_factory
from core.meta_api.queue import create_mutation_task


class RequestBulkPauseTool:
    """Создаёт DRAFT mutation_task на пакетную паузу объявлений.

    Принимает фильтр (spend_gt, cpl_gt, offer_code, ad_ids). Если ad_ids не задан,
    выполняет resolve через данные БД (AdSnapshot). Показывает список объявлений
    до подтверждения.
    """

    name: ClassVar[str] = "request_bulk_pause"
    risk_level: ClassVar[RiskLevel] = RiskLevel.DRAFT_REQUIRED
    schema: ClassVar[dict[str, Any]] = {
        "name": "request_bulk_pause",
        "description": (
            "Создать черновик пакетной остановки объявлений по фильтру "
            "(spend > X, CPL > Y, offer_code, и т.д.). "
            "Показывает список затрагиваемых объявлений перед подтверждением."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string"},
                "filter": {
                    "type": "object",
                    "properties": {
                        "spend_gt": {"type": "number"},
                        "cpl_gt": {"type": "number"},
                        "offer_code": {"type": "string"},
                        "ad_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Точный список ad_id",
                        },
                    },
                },
                "reason": {"type": "string"},
            },
            "required": ["ad_account_id", "filter"],
        },
    }

    async def _resolve_ad_ids(
        self, filter_params: dict[str, Any], session_factory: Any
    ) -> list[str]:
        """Разрешает фильтр в список ad_ids через AdSnapshot из БД.

        Если в фильтре указан явный список ad_ids — возвращает его без запроса к БД.
        Иначе фильтрует AdSnapshot по spend/cpl/offer_code.
        """
        from sqlalchemy import select

        from core.models import AdSnapshot

        # Явный список — используем напрямую
        explicit_ids = filter_params.get("ad_ids")
        if explicit_ids:
            return list(explicit_ids)

        spend_gt: float | None = filter_params.get("spend_gt")
        cpl_gt: float | None = filter_params.get("cpl_gt")
        offer_code: str | None = filter_params.get("offer_code")

        # Если фильтр не задан ни по одному полю — возвращаем пустой список
        if spend_gt is None and cpl_gt is None and offer_code is None:
            return []

        async with session_factory() as db:
            query = select(AdSnapshot.fb_ad_id)

            if spend_gt is not None:
                query = query.where(AdSnapshot.spend >= spend_gt)
            if cpl_gt is not None:
                # cpl = spend / leads_count; фильтруем объявления где цена лида выше порога
                query = query.where(
                    (AdSnapshot.leads > 0) & (AdSnapshot.spend / AdSnapshot.leads >= cpl_gt)
                )
            if offer_code is not None:
                # Матчинг по offer_code — вхождение в имя кампании (case-insensitive)
                query = query.where(AdSnapshot.campaign_name.ilike(f"%{offer_code}%"))

            result = await db.execute(query)
            return [row[0] for row in result.fetchall() if row[0]]

    async def run(self, args: dict[str, Any]) -> str:
        """Создаёт DRAFT mutation_task на bulk-паузу объявлений."""
        ad_account_id: str = args["ad_account_id"]
        filter_params: dict[str, Any] = args.get("filter", {})
        reason: str = args.get("reason", "")

        session_factory = get_session_factory()

        # Разрешаем фильтр в список ad_ids
        ad_ids = await self._resolve_ad_ids(filter_params, session_factory)

        if not ad_ids:
            raise ToolError("По фильтру не найдено объявлений")

        payload: dict[str, Any] = {
            "ad_ids": ad_ids,
            "reason": reason,
            "filter": filter_params,
        }

        async with session_factory() as db:
            task = await create_mutation_task(
                db,
                mutation_kind="bulk_pause",
                # target_id пустой для bulk-операции
                target_id="",
                ad_account_id=ad_account_id,
                payload=payload,
                requested_by="ai_assistant",
                initial_status="DRAFT",
            )
            await db.commit()

        # Формируем список для отображения (не более 20 в тексте)
        ids_preview = ad_ids[:20]
        ids_text = "\n".join(f"  - {ad_id}" for ad_id in ids_preview)
        if len(ad_ids) > 20:
            ids_text += f"\n  ... и ещё {len(ad_ids) - 20} объявлений"

        return (
            f"Черновик создан.\n"
            f"task_id: {task.id}\n"
            f"mutation_kind: bulk_pause\n"
            f"Кабинет: {ad_account_id}\n"
            f"Объявлений к паузе: {len(ad_ids)}\n"
            f"{ids_text}\n"
            f"Причина: {reason or '—'}\n"
            f"Подтвердите в Telegram чтобы исполнить."
        )
