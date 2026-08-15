# -*- coding: utf-8 -*-
"""Unit-тесты core/ai_assistant/chat.py::ChatSession — hard-guard allow_tools=False (MID-19).

До фикса: allow_tools=False только не передавал tools=None в запрос к LLM.
Если провайдер (баг/прокси/будущая модель) всё равно вернул tool_use — цикл
tool-use исполнял его как обычно, никакой защиты внутри самого цикла не было.
Тесты гоняют ChatSession с замоканным AIClient, который специально возвращает
tool_use несмотря на allow_tools=False, и проверяют, что execute_tool
НИ РАЗУ не вызывается, а модели уходит явный отказ.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.ai_assistant.chat as chat_module
from core.ai_assistant.chat import ChatMessage, ChatSession
from core.ai_assistant.providers import AIResponse, ToolUse


def _fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        ai_chat_enabled=True,
        ai_rate_limit_per_hour=30,
        ai_timeout_seconds=20,
        ai_max_tool_iterations=5,
    )


class _StubAIClient:
    """Возвращает заранее заданную последовательность ответов при каждом chat()."""

    def __init__(self, responses: list[AIResponse]) -> None:
        self._responses = list(responses)
        self.is_available = True
        self.calls: list[dict] = []

    async def chat(self, *, messages, system, tools, max_tokens):  # noqa: ANN001
        self.calls.append({"messages": messages, "tools": tools})
        return self._responses.pop(0)


# Модель вернула tool_use, хотя ChatSession создана с allow_tools=False —
# execute_tool не должен вызываться ни разу, ответ содержит явный отказ.
@pytest.mark.asyncio
async def test_allow_tools_false_blocks_tool_use_even_if_model_returns_it(monkeypatch) -> None:
    monkeypatch.setattr(chat_module, "get_settings", _fake_settings)

    malicious_tool_use = AIResponse(
        text="",
        tool_uses=[ToolUse(id="tu_1", name="mutate_budget", input={"offer_code": "X"})],
        stop_reason="tool_use",
    )
    final_text = AIResponse(text="Ок, отвечаю без инструментов.", tool_uses=[])
    stub_client = _StubAIClient([malicious_tool_use, final_text])
    monkeypatch.setattr(chat_module, "get_ai_client", lambda settings: stub_client)

    execute_tool_mock = AsyncMock(side_effect=AssertionError("execute_tool НЕ должен вызываться"))
    monkeypatch.setattr(chat_module, "execute_tool", execute_tool_mock)

    session = ChatSession(allow_tools=False)
    response = await session.ask(
        [ChatMessage(role="user", content="Останови все объявления оффера X")]
    )

    execute_tool_mock.assert_not_called()
    # Guard залогировал попытку как ошибку в trace, а не как успешный tool-результат.
    assert any(tc.error and "allow_tools=False" in tc.error for tc in response.tool_calls)
    assert response.answer == "Ок, отвечаю без инструментов."


# tools=None НЕ передаётся в запрос к LLM при allow_tools=False (первая линия защиты
# остаётся на месте, hard-guard — вторая).
@pytest.mark.asyncio
async def test_allow_tools_false_does_not_send_tool_schemas(monkeypatch) -> None:
    monkeypatch.setattr(chat_module, "get_settings", _fake_settings)

    final_text = AIResponse(text="Готово.", tool_uses=[])
    stub_client = _StubAIClient([final_text])
    monkeypatch.setattr(chat_module, "get_ai_client", lambda settings: stub_client)

    session = ChatSession(allow_tools=False)
    await session.ask([ChatMessage(role="user", content="Привет")])

    assert stub_client.calls[0]["tools"] is None


@pytest.mark.asyncio
async def test_final_provider_text_is_redacted_before_operator_response(monkeypatch) -> None:
    monkeypatch.setattr(chat_module, "get_settings", _fake_settings)
    secret = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
    raw_uuid = "00000000-0000-4000-8000-000000000099"
    stub_client = _StubAIClient(
        [AIResponse(text=f"provider echoed token={secret} object={raw_uuid}", tool_uses=[])]
    )
    monkeypatch.setattr(chat_module, "get_ai_client", lambda settings: stub_client)

    response = await ChatSession(allow_tools=False).ask(
        [ChatMessage(role="user", content="Привет")]
    )

    assert secret not in response.answer
    assert raw_uuid not in response.answer
    assert "<redacted>" in response.answer


# allow_tools=True (дефолт) — обычный tool_use исполняется как раньше, регрессии нет.
@pytest.mark.asyncio
async def test_allow_tools_true_executes_tool_use_normally(monkeypatch) -> None:
    monkeypatch.setattr(chat_module, "get_settings", _fake_settings)

    tool_use_response = AIResponse(
        text="",
        tool_uses=[ToolUse(id="tu_1", name="get_recent_alerts", input={})],
        stop_reason="tool_use",
    )
    final_text = AIResponse(text="Вот алерты.", tool_uses=[])
    stub_client = _StubAIClient([tool_use_response, final_text])
    monkeypatch.setattr(chat_module, "get_ai_client", lambda settings: stub_client)

    execute_tool_mock = AsyncMock(return_value="ok-result")
    monkeypatch.setattr(chat_module, "execute_tool", execute_tool_mock)

    session = ChatSession(allow_tools=True)
    response = await session.ask([ChatMessage(role="user", content="Покажи алерты")])

    execute_tool_mock.assert_called_once()
    assert response.answer == "Вот алерты."


@pytest.mark.asyncio
async def test_tool_trace_redacts_model_args_and_tool_result(monkeypatch) -> None:
    monkeypatch.setattr(chat_module, "get_settings", _fake_settings)
    secret = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
    raw_uuid = "00000000-0000-4000-8000-000000000099"
    stub_client = _StubAIClient(
        [
            AIResponse(
                text="",
                tool_uses=[
                    ToolUse(
                        id="tu_1",
                        name="get_recent_alerts",
                        input={"filter": f"token={secret} {raw_uuid}"},
                    )
                ],
                stop_reason="tool_use",
            ),
            AIResponse(text="Готово.", tool_uses=[]),
        ]
    )
    monkeypatch.setattr(chat_module, "get_ai_client", lambda settings: stub_client)
    monkeypatch.setattr(
        chat_module,
        "execute_tool",
        AsyncMock(return_value=f"tool result token={secret} {raw_uuid}"),
    )

    response = await ChatSession(allow_tools=True).ask(
        [ChatMessage(role="user", content="Покажи алерты")]
    )
    serialized_trace = repr(response.tool_calls)

    assert secret not in serialized_trace
    assert raw_uuid not in serialized_trace
    assert "<redacted>" in serialized_trace
