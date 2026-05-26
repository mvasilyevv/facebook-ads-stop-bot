# -*- coding: utf-8 -*-
"""Tool generate_ad_copy — LLM-генерация текстов рекламного объявления.

System prompt вынесен в `core/ai_assistant/prompts/ad_copy.md` (wave 3).
Параметры конкретного вызова передаются в user-message в виде JSON —
это удобнее для LLM (system = правила, user = данные) и безопаснее
по отношению к format-string injection.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, ClassVar

from core.ai_assistant.prompts import PromptNotFoundError, load_prompt
from core.ai_assistant.tools.base import RiskLevel, ToolError

logger = logging.getLogger(__name__)


class GenerateAdCopyTool:
    """Генерирует 3-5 вариантов текстов объявления через LLM.

    Не вызывает Marketing API и не создаёт задачи в БД.
    Возвращает человекочитаемую строку с вариантами.
    """

    name: ClassVar[str] = "generate_ad_copy"
    risk_level: ClassVar[RiskLevel] = RiskLevel.CREATIVE
    schema: ClassVar[dict[str, Any]] = {
        "name": "generate_ad_copy",
        "description": (
            "Сгенерировать 3 варианта текстов рекламного объявления (primary_text, headline, "
            "description) по описанию оффера. Картинки/видео не генерируются — только тексты."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "offer_code": {
                    "type": "string",
                    "description": "Код оффера, например DRC_CR2",
                },
                "vertical": {
                    "type": "string",
                    "enum": [
                        "igaming",
                        "betting",
                        "crypto",
                        "nutra",
                        "dating",
                        "finance",
                        "other",
                    ],
                    "default": "igaming",
                },
                "country": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 2,
                    "description": "ISO-2 код страны",
                },
                "landing_url_summary": {
                    "type": "string",
                    "description": "Краткое описание лендинга — что предлагается",
                },
                "tone": {
                    "type": "string",
                    "enum": [
                        "curiosity",
                        "social_proof",
                        "urgency",
                        "fear_of_missing",
                        "authority",
                    ],
                    "default": "curiosity",
                },
                "language": {
                    "type": "string",
                    "default": "ru",
                    "description": "ISO-2 код языка текста (en, ru, pt, fr...)",
                },
                "forbidden_words": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Слова которые НЕЛЬЗЯ использовать (для соблюдения политик Meta)",
                },
                "max_variants": {
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 5,
                },
            },
            "required": ["offer_code", "vertical", "country"],
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        """Генерирует варианты текстов через LLM и возвращает форматированный результат."""
        from core.ai_assistant.client import AIUnavailableError, get_ai_client

        # Получаем AI-клиент, проверяем доступность
        ai = get_ai_client()
        if not ai.is_available:
            raise ToolError("AI не настроен — проверь ANTHROPIC_API_KEY / OPENAI_API_KEY в .env")

        # Собираем параметры с дефолтами
        offer_code = str(args.get("offer_code", ""))
        vertical = str(args.get("vertical", "igaming"))
        country = str(args.get("country", "")).upper()
        landing_url_summary = str(args.get("landing_url_summary", "не указано"))
        tone = str(args.get("tone", "curiosity"))
        language = str(args.get("language", "ru"))
        forbidden_words = list(args.get("forbidden_words") or [])
        max_variants = int(args.get("max_variants", 3))

        # Загружаем system-prompt из prompts/ad_copy.md
        try:
            system_prompt = load_prompt("ad_copy")
        except PromptNotFoundError as exc:
            raise ToolError(f"system prompt 'ad_copy' не найден: {exc}") from exc

        # Параметры конкретного вызова — в user-сообщении (JSON)
        user_content = json.dumps(
            {
                "offer_code": offer_code,
                "vertical": vertical,
                "country": country,
                "landing_url_summary": landing_url_summary,
                "tone": tone,
                "language": language,
                "forbidden_words": forbidden_words,
                "max_variants": max_variants,
            },
            ensure_ascii=False,
            indent=2,
        )

        logger.info(
            "generate_ad_copy: оффер=%s гео=%s вертикаль=%s макс_вариантов=%d",
            offer_code,
            country,
            vertical,
            max_variants,
        )

        # Вызываем LLM
        try:
            response = await ai.chat(
                messages=[{"role": "user", "content": user_content}],
                system=system_prompt,
                max_tokens=2048,
            )
        except AIUnavailableError as exc:
            raise ToolError(f"AI не настроен — {exc}") from exc

        raw_text = response.text.strip()

        # Извлекаем JSON из ответа LLM (может содержать markdown-кавычки)
        variants = _parse_json_variants(raw_text)

        # Форматируем вывод
        return _format_variants(variants, offer_code, country)


def _parse_json_variants(raw: str) -> list[dict[str, Any]]:
    """Извлекает список вариантов из ответа LLM.

    Пробует прямой json.loads, затем regex-поиск JSON-массива.
    Бросает ToolError при невалидном JSON.
    """
    # Убираем markdown-блоки ```json ... ```
    cleaned = re.sub(r"```(?:json)?\s*([\s\S]*?)```", r"\1", raw).strip()

    # Попытка 1: весь ответ — валидный JSON
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Попытка 2: regex-поиск первого JSON-массива в строке
    match = re.search(r"(\[[\s\S]*\])", cleaned)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    raise ToolError("AI вернул невалидный JSON — попробуй повторить запрос или упростить параметры")


def _format_variants(variants: list[dict[str, Any]], offer_code: str, country: str) -> str:
    """Форматирует список вариантов в читаемый текст для пользователя."""
    lines: list[str] = [
        f"Сгенерировано {len(variants)} вариант(ов) текста для {offer_code} / {country}:\n"
    ]

    for i, v in enumerate(variants, 1):
        hook_pct = int(float(v.get("predicted_hook_strength", 0)) * 100)
        risk_pct = int(float(v.get("predicted_policy_risk", 0)) * 100)
        lines.append(f"--- Вариант {i} ---")
        lines.append(f"Primary text : {v.get('primary_text', '')}")
        lines.append(f"Headline     : {v.get('headline', '')}")
        lines.append(f"Description  : {v.get('description', '')}")
        lines.append(f"Hook strength: {hook_pct}%  |  Policy risk: {risk_pct}%")
        reasoning = v.get("reasoning", "")
        if reasoning:
            lines.append(f"Почему сработает: {reasoning}")
        lines.append("")

    return "\n".join(lines)
