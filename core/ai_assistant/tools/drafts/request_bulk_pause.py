# -*- coding: utf-8 -*-
"""Tool request_bulk_pause — DRAFT mutation bulk_status_change (PAUSE).

Принимает list of ad_ids ИЛИ filter по offer_code (тогда сначала резолвится через БД).
В payload — итоговый список ad_ids + action=pause.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from sqlalchemy import text

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError
from core.meta_api.queue import create_draft_task
from core.meta_api.schemas import MetaMutationPayload

_MAX_BULK_SIZE = 50


class RequestBulkPauseTool:
    """DRAFT-задача bulk_status_change action=pause для пачки ad_ids.

    Можно передать либо ad_ids напрямую, либо offer_code — тогда tool
    резолвит активные ad_id из таблицы fb_ads через JOIN fb_campaigns
    по word-boundary совпадению offer.code (case-insensitive) в
    campaign_name или ad_name. Substring-ложных совпадений нет
    ('CR' не поймает 'ACRO').
    """

    name: ClassVar[str] = "request_bulk_pause"
    risk_level: ClassVar[RiskLevel] = RiskLevel.DRAFT_REQUIRED
    schema: ClassVar[dict[str, Any]] = {
        "name": "request_bulk_pause",
        "description": (
            "Создать DRAFT задачу на массовое отключение объявлений "
            "(bulk_status_change, action=pause). Передавай либо ad_ids, либо "
            "offer_code (резолвится в ad_ids по word-boundary совпадению "
            "в campaign_name/ad_name, case-insensitive). Max 50 ads за вызов."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": _MAX_BULK_SIZE,
                },
                "offer_code": {"type": "string"},
                "ad_account_id": {"type": "string", "description": "act_X для аудита"},
                "reason": {"type": "string", "maxLength": 200},
            },
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        engine = ctx.require_engine()
        raw_ad_ids = [str(x).strip() for x in (args.get("ad_ids") or []) if x]
        offer_code = (args.get("offer_code") or "").strip()
        ad_account_id = (args.get("ad_account_id") or "").strip() or None
        reason = (args.get("reason") or "")[:200]

        if not raw_ad_ids and not offer_code:
            raise ToolError("Передай ad_ids или offer_code")
        if raw_ad_ids and offer_code:
            raise ToolError("Передай либо ad_ids, либо offer_code, не оба")

        if offer_code:
            ad_ids = await self._resolve_by_offer(engine, offer_code)
            if not ad_ids:
                raise ToolError(
                    f"По офферу {offer_code!r} активных объявлений не нашлось "
                    "(word-boundary совпадение в campaign_name/ad_name)"
                )
        else:
            ad_ids = list({x for x in raw_ad_ids if x.isdigit()})
            if not ad_ids:
                raise ToolError("ad_ids после фильтрации пуст (нужны числовые ID)")

        if len(ad_ids) > _MAX_BULK_SIZE:
            raise ToolError(f"Слишком много ad_ids ({len(ad_ids)} > {_MAX_BULK_SIZE})")

        params: dict[str, Any] = {
            "ad_ids": sorted(ad_ids),
            "action": "pause",
        }
        if offer_code:
            params["resolved_from_offer"] = offer_code
        if reason:
            params["reason"] = reason

        target_id = ad_ids[0] if len(ad_ids) == 1 else f"bulk:{len(ad_ids)}"

        payload = MetaMutationPayload(
            mutation_kind="bulk_status_change",
            target_id=target_id,
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

        preview_ids = ", ".join(ad_ids[:5]) + (" …" if len(ad_ids) > 5 else "")
        source = f"offer={offer_code}" if offer_code else "ad_ids"
        return (
            f"DRAFT создан: task_id={task_id} (bulk_status_change pause, "
            f"{len(ad_ids)} ads, source={source}). "
            f"IDs: {preview_ids}. Подтверди в TG."
        )

    @staticmethod
    async def _resolve_by_offer(engine: Any, offer_code: str) -> list[str]:
        """Найти fb_ad_id для активных объявлений с совпадением offer.code.

        Word-boundary matching через Postgres POSIX regex:
            (^|[^a-z0-9])CODE([^a-z0-9]|$)
        Защита от substring-ложных совпадений (`CR` не поймает `ACRO`).
        re.escape экранирует спецсимволы кода офера (`CR-A`, `CR.2` и т.п.).

        Матчим и по campaign_name, и по ad_name — наш core.observer.queries
        даёт приоритет ad_name при выборе оффера, поэтому покрываем оба
        источника (case-insensitive, regex `~*`).

        fb_campaigns связан через fb_adsets — fb_ads.adset_id → fb_adsets.id
        → fb_adsets.campaign_id → fb_campaigns.id.
        """
        # Pattern собирается на стороне приложения чтобы re.escape отработал.
        escaped = re.escape(offer_code.lower())
        pattern = rf"(^|[^a-z0-9]){escaped}([^a-z0-9]|$)"
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT DISTINCT a.fb_ad_id
                        FROM fb_ads a
                        JOIN fb_adsets s ON s.id = a.adset_id
                        JOIN fb_campaigns c ON c.id = s.campaign_id
                        WHERE (c.campaign_name ~* :pattern OR a.ad_name ~* :pattern)
                          AND a.fb_ad_id IS NOT NULL
                          AND a.is_active = TRUE
                        LIMIT :lim
                        """
                    ),
                    {"pattern": pattern, "lim": _MAX_BULK_SIZE + 10},
                )
            ).all()
        return [str(r[0]) for r in rows if r[0]]
