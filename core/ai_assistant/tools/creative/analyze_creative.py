# -*- coding: utf-8 -*-
"""Tool analyze_creative — LLM-анализ структуры текста рекламного объявления.

System prompt вынесен в `core/ai_assistant/prompts/competitor_extraction.md` (wave 3).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, ClassVar

from core.ai_assistant.prompts import PromptNotFoundError, load_prompt
from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError

logger = logging.getLogger(__name__)


class AnalyzeCreativeTool:
    """Анализирует текст рекламного объявления через LLM.

    Извлекает hook, pain point, value prop, proof elements и оценивает
    риск нарушения политики Meta. Не вызывает Marketing API.
    """

    name: ClassVar[str] = "analyze_creative"
    risk_level: ClassVar[RiskLevel] = RiskLevel.CREATIVE
    schema: ClassVar[dict[str, Any]] = {
        "name": "analyze_creative",
        "description": (
            "Проанализировать существующий креатив (наш или конкурента) — "
            "выделить hook/pain/CTA/proof. Только текст, без изображения."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "primary_text": {"type": "string"},
                "headline": {"type": "string"},
                "description": {"type": "string"},
                "cta_type": {
                    "type": "string",
                    "description": "Тип CTA (например LEARN_MORE, SHOP_NOW)",
                },
            },
            "required": ["primary_text"],
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        """Анализирует текст рекламы через LLM и возвращает структурированный результат."""
        from core.ai_assistant.client import AIUnavailableError, get_ai_client

        _ = ctx  # creative tool не использует ctx-зависимости
        ai = get_ai_client()
        if not ai.is_available:
            raise ToolError("AI не настроен — проверь ANTHROPIC_API_KEY / OPENAI_API_KEY в .env")

        # Собираем параметры
        primary_text = str(args.get("primary_text", ""))
        headline = str(args.get("headline", ""))
        description = str(args.get("description", ""))
        cta_type = str(args.get("cta_type", ""))

        if not primary_text.strip():
            raise ToolError("primary_text не может быть пустым")

        # Загружаем system-prompt из prompts/competitor_extraction.md
        try:
            system_prompt = load_prompt("competitor_extraction")
        except PromptNotFoundError as exc:
            raise ToolError(f"system prompt 'competitor_extraction' не найден: {exc}") from exc

        # Параметры — в user-сообщении (JSON)
        user_content = json.dumps(
            {
                "primary_text": primary_text,
                "headline": headline,
                "description": description,
                "cta_type": cta_type or "не указан",
            },
            ensure_ascii=False,
            indent=2,
        )

        logger.info(
            "analyze_creative: primary_text=%s...",
            primary_text[:50],
        )

        # Вызываем LLM
        try:
            response = await ai.chat(
                messages=[{"role": "user", "content": user_content}],
                system=system_prompt,
                max_tokens=1024,
            )
        except AIUnavailableError as exc:
            raise ToolError(f"AI не настроен — {exc}") from exc

        raw_text = response.text.strip()

        # Извлекаем и форматируем JSON-анализ
        analysis = _parse_json_analysis(raw_text)
        return _format_analysis(analysis, primary_text)


def _parse_json_analysis(raw: str) -> dict[str, Any]:
    """Извлекает словарь анализа из ответа LLM.

    Пробует прямой json.loads, затем regex-поиск JSON-объекта.
    Бросает ToolError при невалидном JSON.
    """
    # Убираем markdown-блоки ```json ... ```
    cleaned = re.sub(r"```(?:json)?\s*([\s\S]*?)```", r"\1", raw).strip()

    # Попытка 1: весь ответ — валидный JSON-объект
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Попытка 2: regex-поиск первого JSON-объекта в строке
    match = re.search(r"(\{[\s\S]*\})", cleaned)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    raise ToolError("AI вернул невалидный JSON — попробуй повторить запрос")


def _format_analysis(analysis: dict[str, Any], primary_text: str) -> str:
    """Форматирует словарь анализа в читаемый текст для пользователя."""
    risk_pct = int(float(analysis.get("policy_risk", 0)) * 100)

    proof = analysis.get("proof_elements") or []
    proof_str = ", ".join(proof) if proof else "не найдены"

    urgency = analysis.get("urgency_signals") or []
    urgency_filtered = [x for x in urgency if x and x != "null"]
    urgency_str = ", ".join(urgency_filtered) if urgency_filtered else "нет"

    lines = [
        f'Анализ объявления: "{primary_text[:60]}{"..." if len(primary_text) > 60 else ""}"\n',
        f"Hook          : {analysis.get('hook', '')}",
        f"Тип hook      : {analysis.get('hook_type', '')}",
        f"Pain point    : {analysis.get('pain_point', '')}",
        f"Value prop    : {analysis.get('value_prop', '')}",
        f"Proof elements: {proof_str}",
        f"Urgency       : {urgency_str}",
        f"CTA strength  : {analysis.get('cta_strength', '')}",
        f"Регистр       : {analysis.get('language_register', '')}",
        f"ЦА            : {analysis.get('target_persona_guess', '')}",
        f"Policy risk   : {risk_pct}%",
    ]

    return "\n".join(lines)
