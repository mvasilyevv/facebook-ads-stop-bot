# -*- coding: utf-8 -*-
"""Tool request_create_campaign — черновик создания новой кампании из CampaignSpec.

NL parser активирован в wave 3 — natural_language_description парсится через
LLM по system prompt `core/ai_assistant/prompts/creator_nl_parser.md`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, ClassVar

from core.ai_assistant.prompts import PromptNotFoundError, load_prompt
from core.ai_assistant.tools.base import RiskLevel, ToolError
from core.db import get_session_factory
from core.meta_api.queue import create_mutation_task

logger = logging.getLogger(__name__)


class RequestCreateCampaignTool:
    """Создаёт DRAFT mutation_task на создание новой кампании.

    Принимает либо structured `spec_summary`, либо `natural_language_description` —
    второй парсится LLM-ом через prompt `creator_nl_parser`.
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
                        "Альтернатива spec_summary — текст 'создай кампанию по DRC_CR2 на Гваделупу...'. "
                        "Парсится LLM-ом в structured spec_summary (creator_nl_parser prompt)."
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

        # NL parser: если задано описание без structured spec — парсим через LLM
        nl_warnings: list[str] = []
        if natural_language and not spec_summary:
            spec_summary, nl_warnings = await _parse_nl_description(natural_language)

        if not spec_summary:
            raise ToolError(
                "Укажите spec_summary (структурированные параметры кампании) или "
                "natural_language_description (свободное текстовое описание)."
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

        lines = [
            "Черновик создан.",
            f"task_id: {task.id}",
            "mutation_kind: create_campaign",
            f"Кабинет: {ad_account_id}",
            f"Оффер: {offer_code}",
            f"Цель: {objective}",
            f"Страны: {countries_text}",
            f"Бюджет: {budget_text}",
            f"Причина: {reason or '—'}",
        ]
        if nl_warnings:
            lines.append("Предупреждения NL-парсера: " + "; ".join(nl_warnings))
        lines.append("Подтвердите в Telegram чтобы исполнить.")
        return "\n".join(lines)


async def _parse_nl_description(text: str) -> tuple[dict[str, Any], list[str]]:
    """Парсит свободное описание кампании через LLM (creator_nl_parser prompt).

    Возвращает кортеж (spec_summary, warnings). Бросает ToolError если LLM
    вернул `_errors` или невалидный JSON.
    """
    from core.ai_assistant.client import AIUnavailableError, get_ai_client

    ai = get_ai_client()
    if not ai.is_available:
        raise ToolError(
            "AI не настроен — natural_language_description требует ANTHROPIC_API_KEY "
            "или OPENAI_API_KEY в .env. Используйте spec_summary вместо описания."
        )

    try:
        system_prompt = load_prompt("creator_nl_parser")
    except PromptNotFoundError as exc:
        raise ToolError(f"system prompt 'creator_nl_parser' не найден: {exc}") from exc

    logger.info("creator_nl_parser: разбираем описание длины %d", len(text))

    try:
        response = await ai.chat(
            messages=[{"role": "user", "content": text}],
            system=system_prompt,
            max_tokens=512,
        )
    except AIUnavailableError as exc:
        raise ToolError(f"AI недоступен для NL-парсинга: {exc}") from exc

    parsed = _extract_json_object(response.text.strip())

    errors: list[str] = list(parsed.pop("_errors", None) or [])
    warnings: list[str] = list(parsed.pop("_warnings", None) or [])

    if errors:
        raise ToolError("NL parser не смог распознать описание: " + "; ".join(errors))

    if not parsed.get("offer_code"):
        raise ToolError(
            "NL parser не извлёк offer_code из описания. "
            "Уточните: 'оффер DRC_CR2' / 'по офферу BetOnline_FR'."
        )

    return parsed, warnings


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Извлекает первый JSON-объект из ответа LLM.

    Пробует прямой json.loads, затем regex-поиск. Бросает ToolError при невалидном JSON.
    """
    # Убираем markdown-блоки ```json ... ```
    cleaned = re.sub(r"```(?:json)?\s*([\s\S]*?)```", r"\1", raw).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"(\{[\s\S]*\})", cleaned)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    raise ToolError(
        "NL parser вернул невалидный JSON — попробуйте упростить описание "
        "или использовать structured spec_summary."
    )
