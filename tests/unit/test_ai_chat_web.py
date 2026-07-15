# -*- coding: utf-8 -*-
"""Unit-тесты веб-чата: risk-фильтр ChatSession + роутер POST /ai/chat."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.api.routers.v1.ai_chat_web import ai_chat
from apps.api.routers.v1.schemas.ai_chat import AIChatRequest, ChatMessageIn
from core.ai_assistant.chat import ChatMessage, ChatResponse, ChatSession
from core.ai_assistant.providers import AIResponse, ToolUse
from core.ai_assistant.tools.base import RiskLevel

_WEB_RISKS = frozenset({RiskLevel.READ_ONLY, RiskLevel.CREATIVE})


def _request_mock() -> MagicMock:
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock()
    req.client.host = "10.0.0.7"
    return req


def _settings_mock() -> MagicMock:
    s = MagicMock()
    s.trust_proxy_headers = False
    s.trusted_proxy_count = 1
    s.anthropic_api_key = None
    s.openai_model = "gpt-5.6-luna"
    return s


# Risk-фильтр: schemas веб-канала не содержат draft-инструментов (request_*)
def test_web_session_schemas_exclude_drafts() -> None:
    session = ChatSession(allow_tools=True, allowed_risk_levels=_WEB_RISKS)
    assert session._allowed_tool_names is not None
    assert not any(n.startswith("request_") for n in session._allowed_tool_names)
    assert "get_worker_health" in session._allowed_tool_names
    assert "analyze_creative" in session._allowed_tool_names


# Guard исполнения: модель галлюцинирует draft-инструмент → отказ, НЕ исполнение
@pytest.mark.asyncio
async def test_web_session_blocks_hallucinated_draft_tool() -> None:
    # Первый ответ — tool_use запрещённого request_bulk_pause, второй — обычный текст
    fake_ai = MagicMock()
    fake_ai.is_available = True
    fake_ai.chat = AsyncMock(
        side_effect=[
            AIResponse(
                text="",
                tool_uses=[ToolUse(id="t1", name="request_bulk_pause", input={"offer_code": "X"})],
            ),
            AIResponse(text="Понял, действия доступны в Telegram."),
        ]
    )
    with (
        patch("core.ai_assistant.chat.get_ai_client", return_value=fake_ai),
        patch("core.ai_assistant.chat.execute_tool", new=AsyncMock()) as exec_tool,
    ):
        session = ChatSession(allow_tools=True, allowed_risk_levels=_WEB_RISKS)
        resp = await session.ask([ChatMessage("user", "поставь X на паузу")], client_key="web-test")
    exec_tool.assert_not_awaited()  # запрещённый инструмент НЕ исполнялся
    assert resp.tool_calls[0].error is not None
    assert "недоступен" in resp.tool_calls[0].error


# Роутер: превышение Redis-лимита → 429 до обращения к AI
@pytest.mark.asyncio
async def test_endpoint_rate_limited_429() -> None:
    from core.ai_assistant.tools._ratelimit import RateLimitExceeded

    with patch(
        "apps.api.routers.v1.ai_chat_web.check_and_increment",
        new=AsyncMock(side_effect=RateLimitExceeded("лимит")),
    ):
        resp = await ai_chat(
            _request_mock(),
            AIChatRequest(messages=[ChatMessageIn(role="user", content="привет")]),
            engine=MagicMock(),
            redis=MagicMock(),
            settings=_settings_mock(),
        )
    assert resp.status_code == 429


# Роутер: AI-провайдеры не настроены → 503
@pytest.mark.asyncio
async def test_endpoint_ai_unavailable_503() -> None:
    ai = MagicMock()
    ai.is_available = False
    with (
        patch("apps.api.routers.v1.ai_chat_web.check_and_increment", new=AsyncMock()),
        patch("apps.api.routers.v1.ai_chat_web.get_ai_client", return_value=ai),
    ):
        resp = await ai_chat(
            _request_mock(),
            AIChatRequest(messages=[ChatMessageIn(role="user", content="привет")]),
            engine=MagicMock(),
            redis=MagicMock(),
            settings=_settings_mock(),
        )
    assert resp.status_code == 503


# Роутер: happy path — ответ ассистента + read-only канал в ChatSession
@pytest.mark.asyncio
async def test_endpoint_happy_path() -> None:
    ai = MagicMock()
    ai.is_available = True
    session = MagicMock()
    session.ask = AsyncMock(return_value=ChatResponse(answer="Кабинет спокоен."))
    with (
        patch("apps.api.routers.v1.ai_chat_web.check_and_increment", new=AsyncMock()),
        patch("apps.api.routers.v1.ai_chat_web.get_ai_client", return_value=ai),
        patch("apps.api.routers.v1.ai_chat_web.ChatSession", return_value=session) as cs,
    ):
        resp = await ai_chat(
            _request_mock(),
            AIChatRequest(
                messages=[
                    ChatMessageIn(role="user", content="как дела?"),
                    ChatMessageIn(role="assistant", content="норм"),
                    ChatMessageIn(role="user", content="а сейчас?"),
                ]
            ),
            engine=MagicMock(),
            redis=MagicMock(),
            settings=_settings_mock(),
        )
    assert resp.status_code == 200
    data = json.loads(resp.body)
    assert data["answer"] == "Кабинет спокоен."
    assert data["model"] == "gpt-5.6-luna"
    # Канал ограничен read-only+creative и использует веб-скил
    kwargs = cs.call_args.kwargs
    assert kwargs["allowed_risk_levels"] == _WEB_RISKS
    assert "web_chat" in kwargs["skills"]
    # История дошла целиком (3 сообщения)
    assert len(session.ask.call_args.args[0]) == 3


def _pulse_redis(cached: str | None = None) -> MagicMock:
    r = MagicMock()
    r.get = AsyncMock(return_value=cached)
    r.set = AsyncMock()
    return r


# Пульс: сигналов нет → important=false, AI-текста нет, результат закэширован на час
@pytest.mark.asyncio
async def test_pulse_quiet_cached() -> None:
    from apps.api.routers.v1.ai_chat_web import ai_pulse

    redis = _pulse_redis()
    with patch(
        "apps.api.routers.v1.ai_chat_web.build_pulse", new=AsyncMock(return_value=None)
    ) as bp:
        resp = await ai_pulse(
            _request_mock(), engine=MagicMock(), redis=redis, settings=_settings_mock()
        )
    data = json.loads(resp.body)
    assert data["important"] is False
    assert data["text"] is None
    # html=False — веб-формат без Telegram-тегов
    assert bp.call_args.kwargs["html"] is False
    redis.set.assert_awaited()  # «тихий» час тоже кэшируется


# Пульс: сигналы есть → important=true с текстом отчёта
@pytest.mark.asyncio
async def test_pulse_important_returns_text() -> None:
    from apps.api.routers.v1.ai_chat_web import ai_pulse

    with patch(
        "apps.api.routers.v1.ai_chat_web.build_pulse",
        new=AsyncMock(return_value="2 стопа за час: CR2_CR002 (cpl_stop)."),
    ):
        resp = await ai_pulse(
            _request_mock(), engine=MagicMock(), redis=_pulse_redis(), settings=_settings_mock()
        )
    data = json.loads(resp.body)
    assert data["important"] is True
    assert "2 стопа" in data["text"]


# Пульс: повторный опрос в тот же час отдаёт кэш, build_pulse не вызывается
@pytest.mark.asyncio
async def test_pulse_cache_hit_skips_rebuild() -> None:
    from apps.api.routers.v1.ai_chat_web import ai_pulse

    cached = json.dumps({"important": False, "text": None, "generated_at": "2026-07-15T13:00:00"})
    with patch("apps.api.routers.v1.ai_chat_web.build_pulse", new=AsyncMock()) as bp:
        resp = await ai_pulse(
            _request_mock(),
            engine=MagicMock(),
            redis=_pulse_redis(cached=cached),
            settings=_settings_mock(),
        )
    bp.assert_not_awaited()
    assert json.loads(resp.body)["important"] is False
