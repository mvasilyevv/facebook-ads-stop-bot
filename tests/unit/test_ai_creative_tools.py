# -*- coding: utf-8 -*-
"""Unit-тесты для CREATIVE tools: GenerateAdCopyTool, AnalyzeCreativeTool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.ai_assistant.tools.base import RiskLevel, ToolError

# ---------------------------------------------------------------------------
# Хелперы для создания мок-ответа AIResponse
# ---------------------------------------------------------------------------


def _make_ai_response(text: str):
    """Создать минимальный объект AIResponse с заданным текстом."""
    response = MagicMock()
    response.text = text
    response.has_tool_uses = False
    return response


def _make_ai_client(response_text: str):
    """Создать мок AIClient, возвращающий заданный текст."""
    ai = MagicMock()
    ai.is_available = True
    ai.chat = AsyncMock(return_value=_make_ai_response(response_text))
    return ai


# ---------------------------------------------------------------------------
# Тесты регистрации tools в GLOBAL_REGISTRY
# ---------------------------------------------------------------------------


# Оба CREATIVE-tool должны появиться в GLOBAL_REGISTRY после импорта пакета.
def test_creative_tools_registered_in_global_registry():
    import core.ai_assistant.tools.creative  # noqa: F401  — side-effect import
    from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

    names = set(GLOBAL_REGISTRY.list_names())
    assert "generate_ad_copy" in names, "generate_ad_copy должен быть зарегистрирован"
    assert "analyze_creative" in names, "analyze_creative должен быть зарегистрирован"


# Оба tool должны иметь risk_level=CREATIVE.
def test_creative_tools_have_creative_risk_level():
    from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

    gen_tool = GLOBAL_REGISTRY.get("generate_ad_copy")
    ana_tool = GLOBAL_REGISTRY.get("analyze_creative")
    assert gen_tool is not None
    assert ana_tool is not None
    assert gen_tool.risk_level == RiskLevel.CREATIVE
    assert ana_tool.risk_level == RiskLevel.CREATIVE


# ---------------------------------------------------------------------------
# Тесты GenerateAdCopyTool
# ---------------------------------------------------------------------------


# Валидные аргументы → промпт сформирован корректно, AI вернул JSON → строка с вариантами.
@pytest.mark.asyncio
async def test_generate_ad_copy_valid_args_returns_formatted_string():
    variants = [
        {
            "primary_text": "Попробуй сегодня",
            "headline": "Лучший выбор",
            "description": "Узнай больше",
            "predicted_hook_strength": 0.8,
            "predicted_policy_risk": 0.1,
            "reasoning": "Простой и понятный призыв к действию",
        }
    ]
    ai_mock = _make_ai_client(json.dumps(variants))

    from core.ai_assistant.tools.creative.generate_ad_copy import GenerateAdCopyTool

    tool = GenerateAdCopyTool()

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        result = await tool.run({"offer_code": "DRC_CR2", "vertical": "igaming", "country": "GP"})

    assert "DRC_CR2" in result
    assert "GP" in result
    assert "Попробуй сегодня" in result
    assert "Лучший выбор" in result


# Невалидный JSON в ответе AI → ToolError с сообщением про "невалидный JSON".
@pytest.mark.asyncio
async def test_generate_ad_copy_invalid_json_raises_tool_error():
    ai_mock = _make_ai_client("Это не JSON вообще!")

    from core.ai_assistant.tools.creative.generate_ad_copy import GenerateAdCopyTool

    tool = GenerateAdCopyTool()

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        with pytest.raises(ToolError, match="невалидный JSON"):
            await tool.run({"offer_code": "DRC_CR2", "vertical": "igaming", "country": "GP"})


# forbidden_words в аргументах → эти слова попадают в промпт.
@pytest.mark.asyncio
async def test_generate_ad_copy_forbidden_words_in_prompt():
    """Проверяем, что запрещённые слова из аргументов попадают в сформированный промпт."""
    variants = [
        {
            "primary_text": "Текст объявления",
            "headline": "Заголовок",
            "description": "Описание",
            "predicted_hook_strength": 0.7,
            "predicted_policy_risk": 0.2,
            "reasoning": "Работает",
        }
    ]
    ai_mock = _make_ai_client(json.dumps(variants))

    from core.ai_assistant.tools.creative.generate_ad_copy import GenerateAdCopyTool

    tool = GenerateAdCopyTool()
    captured_prompt: list[str] = []

    async def _capture_chat(*, messages, max_tokens, **_kwargs):
        captured_prompt.append(messages[0]["content"])
        return _make_ai_response(json.dumps(variants))

    ai_mock.chat = _capture_chat

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        await tool.run(
            {
                "offer_code": "TEST",
                "vertical": "igaming",
                "country": "RU",
                "forbidden_words": ["казино", "выиграй"],
            }
        )

    assert captured_prompt, "Промпт должен быть захвачен"
    prompt_text = captured_prompt[0]
    assert "казино" in prompt_text, "Запрещённое слово 'казино' должно быть в промпте"
    assert "выиграй" in prompt_text, "Запрещённое слово 'выиграй' должно быть в промпте"


# max_variants=5 → промпт содержит "5".
@pytest.mark.asyncio
async def test_generate_ad_copy_max_variants_in_prompt():
    """Проверяем, что параметр max_variants=5 отражается в промпте."""
    variants = [
        {
            "primary_text": f"Текст {i}",
            "headline": f"H{i}",
            "description": f"D{i}",
            "predicted_hook_strength": 0.5,
            "predicted_policy_risk": 0.1,
            "reasoning": "ok",
        }
        for i in range(5)
    ]
    captured_prompt: list[str] = []

    async def _capture_chat(*, messages, max_tokens, **_kwargs):
        captured_prompt.append(messages[0]["content"])
        return _make_ai_response(json.dumps(variants))

    ai_mock = MagicMock()
    ai_mock.is_available = True
    ai_mock.chat = _capture_chat

    from core.ai_assistant.tools.creative.generate_ad_copy import GenerateAdCopyTool

    tool = GenerateAdCopyTool()

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        result = await tool.run(
            {
                "offer_code": "TST",
                "vertical": "finance",
                "country": "US",
                "max_variants": 5,
            }
        )

    assert captured_prompt
    assert "5" in captured_prompt[0], "Число 5 должно присутствовать в промпте"
    # Все 5 вариантов отображены в результате
    assert "Вариант 5" in result


# AI client недоступен (is_available=False) → ToolError "AI не настроен".
@pytest.mark.asyncio
async def test_generate_ad_copy_ai_unavailable_raises_tool_error():
    """Если AI-клиент не настроен, tool должен вернуть ToolError."""
    ai_mock = MagicMock()
    ai_mock.is_available = False

    from core.ai_assistant.tools.creative.generate_ad_copy import GenerateAdCopyTool

    tool = GenerateAdCopyTool()

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        with pytest.raises(ToolError, match="AI не настроен"):
            await tool.run({"offer_code": "X", "vertical": "other", "country": "FR"})


# JSON обёрнут в markdown-кавычки — должен парситься корректно.
@pytest.mark.asyncio
async def test_generate_ad_copy_markdown_json_parsed():
    """LLM часто оборачивает JSON в ```json ... ``` — проверяем, что это обрабатывается."""
    variants = [
        {
            "primary_text": "Markdown test",
            "headline": "H",
            "description": "D",
            "predicted_hook_strength": 0.6,
            "predicted_policy_risk": 0.05,
            "reasoning": "markdown",
        }
    ]
    md_json = f"```json\n{json.dumps(variants)}\n```"
    ai_mock = _make_ai_client(md_json)

    from core.ai_assistant.tools.creative.generate_ad_copy import GenerateAdCopyTool

    tool = GenerateAdCopyTool()

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        result = await tool.run({"offer_code": "MKD", "vertical": "crypto", "country": "DE"})

    assert "Markdown test" in result


# ---------------------------------------------------------------------------
# Тесты AnalyzeCreativeTool
# ---------------------------------------------------------------------------


# Полные поля → промпт собран, mocked AI → JSON → читаемый анализ.
@pytest.mark.asyncio
async def test_analyze_creative_full_fields_returns_analysis():
    analysis = {
        "hook": "Узнай как заработать без риска",
        "hook_type": "curiosity",
        "pain_point": "Нет пассивного дохода",
        "value_prop": "Простая схема заработка",
        "proof_elements": ["10 000+ пользователей"],
        "urgency_signals": ["только сегодня"],
        "cta_strength": "hard",
        "language_register": "casual",
        "target_persona_guess": "Мужчина 25-40 лет, ищет доп. заработок",
        "policy_risk": 0.3,
    }
    ai_mock = _make_ai_client(json.dumps(analysis))

    from core.ai_assistant.tools.creative.analyze_creative import AnalyzeCreativeTool

    tool = AnalyzeCreativeTool()

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        result = await tool.run(
            {
                "primary_text": "Узнай как заработать без риска",
                "headline": "Простой заработок",
                "description": "Для всех",
                "cta_type": "LEARN_MORE",
            }
        )

    assert "curiosity" in result
    assert "Policy risk" in result
    assert "30%" in result
    assert "10 000+ пользователей" in result


# Минимальные поля (только primary_text) — tool работает без ошибок.
@pytest.mark.asyncio
async def test_analyze_creative_minimal_fields_works():
    """Достаточно только primary_text — остальные поля необязательны."""
    analysis = {
        "hook": "Зацепка",
        "hook_type": "greed",
        "pain_point": "Мало денег",
        "value_prop": "Деньги",
        "proof_elements": [],
        "urgency_signals": [],
        "cta_strength": "soft",
        "language_register": "formal",
        "target_persona_guess": "Все",
        "policy_risk": 0.1,
    }
    ai_mock = _make_ai_client(json.dumps(analysis))

    from core.ai_assistant.tools.creative.analyze_creative import AnalyzeCreativeTool

    tool = AnalyzeCreativeTool()

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        result = await tool.run({"primary_text": "Заработай быстро и легко"})

    assert "greed" in result
    assert "Policy risk" in result


# AI client недоступен → ToolError "AI не настроен".
@pytest.mark.asyncio
async def test_analyze_creative_ai_unavailable_raises_tool_error():
    """Если AI-клиент не настроен, tool должен вернуть ToolError."""
    ai_mock = MagicMock()
    ai_mock.is_available = False

    from core.ai_assistant.tools.creative.analyze_creative import AnalyzeCreativeTool

    tool = AnalyzeCreativeTool()

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        with pytest.raises(ToolError, match="AI не настроен"):
            await tool.run({"primary_text": "Тест"})


# Невалидный JSON в ответе AI → ToolError.
@pytest.mark.asyncio
async def test_analyze_creative_invalid_json_raises_tool_error():
    ai_mock = _make_ai_client("Отвечаю прозой, не JSON.")

    from core.ai_assistant.tools.creative.analyze_creative import AnalyzeCreativeTool

    tool = AnalyzeCreativeTool()

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        with pytest.raises(ToolError, match="невалидный JSON"):
            await tool.run({"primary_text": "Тест объявления"})


# Пустой primary_text → ToolError до вызова AI.
@pytest.mark.asyncio
async def test_analyze_creative_empty_primary_text_raises_tool_error():
    """Пустой primary_text отклоняется до обращения к LLM."""
    ai_mock = _make_ai_client("{}")

    from core.ai_assistant.tools.creative.analyze_creative import AnalyzeCreativeTool

    tool = AnalyzeCreativeTool()

    with patch("core.ai_assistant.client.get_ai_client", return_value=ai_mock):
        with pytest.raises(ToolError, match="primary_text не может быть пустым"):
            await tool.run({"primary_text": "   "})
