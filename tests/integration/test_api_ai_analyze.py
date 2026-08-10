# -*- coding: utf-8 -*-
"""Интеграционные тесты: POST /api/ai/analyze.

Все тесты мокируют ChatSession.ask — без реальных AI-провайдеров.
Redis — fakeredis из conftest.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.deps import get_redis, get_settings
from apps.api.main import create_app
from core.config import Settings


def _make_app(redis=None, settings=None):
    """Собрать FastAPI с подменёнными Redis и Settings."""
    app = create_app()
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    return app


def _settings_no_ai() -> Settings:
    """Settings без AI-ключей → провайдер не настроен."""
    return Settings(anthropic_api_key="", openai_api_key="")


def _settings_with_anthropic() -> Settings:
    """Settings с anthropic_api_key → primary провайдер активен."""
    return Settings(anthropic_api_key="sk-ant-test-key", openai_api_key="")


def _settings_with_openai() -> Settings:
    """Settings только с openai_api_key → fallback провайдер."""
    return Settings(anthropic_api_key="", openai_api_key="sk-openai-test-key")


def _valid_body(**kwargs) -> dict:
    """Базовое тело запроса."""
    base = {
        "block_type": "dashboard_overview",
        "scope_key": "global",
        "force_refresh": False,
        "client_data": None,
    }
    base.update(kwargs)
    return base


# ─────────────────────── Без AI-ключей → 503 ─────────────────────────────────


# Нет ни одного AI-провайдера → 503 с понятным сообщением
@pytest.mark.asyncio
async def test_ai_analyze_no_providers_503(fake_redis_client, monkeypatch) -> None:
    """Без ANTHROPIC_API_KEY и OPENAI_API_KEY → 503 «не настроены»."""
    # Сбрасываем синглтон AIClient чтобы он не кэшировал предыдущие настройки
    monkeypatch.setattr("core.ai_assistant.client._client_singleton", None)

    app = _make_app(redis=fake_redis_client, settings=_settings_no_ai())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/ai/analyze", json=_valid_body())

    assert resp.status_code == 503
    assert resp.json()["code"] == "ai_unavailable"
    assert "не настроены" in resp.json()["message"].lower()


# ─────────────────────── Happy path (mock ChatSession) ────────────────────────


# Успешный ответ → 200 + analysis_text + from_cache=false
@pytest.mark.asyncio
async def test_ai_analyze_happy(fake_redis_client, monkeypatch) -> None:
    """Mock ChatSession.ask → 200, analysis_text присутствует, from_cache=false."""
    from core.ai_assistant.chat import ChatResponse

    monkeypatch.setattr("core.ai_assistant.client._client_singleton", None)

    mock_response = ChatResponse(answer="Анализ: всё хорошо, CTR в норме.", tool_calls=[])

    async def _fake_ask(self, history, *, client_key="default", **kw):
        return mock_response

    monkeypatch.setattr("core.ai_assistant.chat.ChatSession.ask", _fake_ask)

    # Нужен «настроенный» клиент — делаем is_available=True через мок AIClient
    from core.ai_assistant.client import AIClient

    fake_client = AIClient(primary=None, fallback=None)
    fake_client._primary = object()  # имитируем наличие провайдера
    monkeypatch.setattr("core.ai_assistant.client._client_singleton", fake_client)

    app = _make_app(redis=fake_redis_client, settings=_settings_with_anthropic())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/ai/analyze", json=_valid_body())

    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis_text"] == "Анализ: всё хорошо, CTR в норме."
    assert body["from_cache"] is False
    assert body["block_type"] == "dashboard_overview"
    assert body["scope_key"] == "global"
    assert "generated_at" in body


# ─────────────────────── Redis-кэш ───────────────────────────────────────────


# 2-й запрос возвращает from_cache=true (данные из Redis)
@pytest.mark.asyncio
async def test_ai_analyze_cache_hit(fake_redis_client, monkeypatch) -> None:
    """Второй идентичный запрос берётся из Redis-кэша, from_cache=True."""
    # Кладём данные в кэш вручную
    cache_key = "ai:cache:analyze:dashboard_overview:global"
    cached_payload = {
        "block_type": "dashboard_overview",
        "scope_key": "global",
        "analysis_text": "Кэшированный ответ",
        "from_cache": False,
        "generated_at": "2025-01-01T12:00:00+00:00",
        "model": "claude-sonnet-4.6",
    }
    await fake_redis_client.set(cache_key, json.dumps(cached_payload), ex=600)

    monkeypatch.setattr("core.ai_assistant.client._client_singleton", None)

    # Клиент «настроен», чтобы пройти проверку is_available
    from core.ai_assistant.client import AIClient

    fake_client = AIClient(primary=None, fallback=None)
    fake_client._primary = object()
    monkeypatch.setattr("core.ai_assistant.client._client_singleton", fake_client)

    app = _make_app(redis=fake_redis_client, settings=_settings_with_anthropic())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/ai/analyze", json=_valid_body(force_refresh=False))

    assert resp.status_code == 200
    body = resp.json()
    assert body["from_cache"] is True
    assert body["analysis_text"] == "Кэшированный ответ"


# force_refresh=true → кэш игнорируется, новый запрос к AI
@pytest.mark.asyncio
async def test_ai_analyze_force_refresh(fake_redis_client, monkeypatch) -> None:
    """force_refresh=true → ChatSession вызывается, кэш перезаписывается."""
    # Кладём устаревший кэш
    cache_key = "ai:cache:analyze:dashboard_overview:global"
    old_payload = {
        "block_type": "dashboard_overview",
        "scope_key": "global",
        "analysis_text": "Старый кэш",
        "from_cache": False,
        "generated_at": "2020-01-01T00:00:00+00:00",
        "model": "claude-sonnet-4.6",
    }
    await fake_redis_client.set(cache_key, json.dumps(old_payload), ex=600)

    from core.ai_assistant.chat import ChatResponse
    from core.ai_assistant.client import AIClient

    fake_client = AIClient(primary=None, fallback=None)
    fake_client._primary = object()
    monkeypatch.setattr("core.ai_assistant.client._client_singleton", fake_client)

    ask_called = []

    async def _fake_ask(self, history, *, client_key="default", **kw):
        ask_called.append(True)
        return ChatResponse(answer="Свежий ответ после force_refresh", tool_calls=[])

    monkeypatch.setattr("core.ai_assistant.chat.ChatSession.ask", _fake_ask)

    app = _make_app(redis=fake_redis_client, settings=_settings_with_anthropic())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/ai/analyze", json=_valid_body(force_refresh=True))

    assert resp.status_code == 200
    assert len(ask_called) == 1
    body = resp.json()
    assert body["analysis_text"] == "Свежий ответ после force_refresh"
    assert body["from_cache"] is False


# ─────────────────────── Валидация ───────────────────────────────────────────


# Невалидный block_type → 422
@pytest.mark.asyncio
async def test_ai_analyze_invalid_block_type(fake_redis_client) -> None:
    """Неизвестный block_type → 422 от Pydantic-валидатора."""
    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/ai/analyze",
            json={
                "block_type": "unknown_block_xyz",
                "scope_key": "global",
            },
        )
    assert resp.status_code == 422


# ─────────────────────── Rate-limit ──────────────────────────────────────────


# Превышение Redis rate-limit → 429
@pytest.mark.asyncio
async def test_ai_analyze_rate_limit_exceeded(fake_redis_client, monkeypatch) -> None:
    """check_and_increment бросает RateLimitExceeded → endpoint отдаёт 429."""
    from core.ai_assistant.client import AIClient
    from core.ai_assistant.tools._ratelimit import RateLimitExceeded

    fake_client = AIClient(primary=None, fallback=None)
    fake_client._primary = object()
    monkeypatch.setattr("core.ai_assistant.client._client_singleton", fake_client)
    monkeypatch.setattr("core.ai_assistant.chat.ChatSession.ask", AsyncMock())

    # Redis-backed rate-limit исчерпан — мокаем check_and_increment на отказ.
    monkeypatch.setattr(
        "apps.api.routers.v1.ai_analyze.check_and_increment",
        AsyncMock(side_effect=RateLimitExceeded("лимит исчерпан")),
    )

    app = _make_app(redis=fake_redis_client, settings=_settings_with_anthropic())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/ai/analyze", json=_valid_body())

    assert resp.status_code == 429


# ─────────────────────── Ключ кэша ───────────────────────────────────────────


# Ключ кэша формируется как ai:cache:analyze:{block_type}:{scope_key}
@pytest.mark.asyncio
async def test_ai_analyze_cache_key_format(fake_redis_client, monkeypatch) -> None:
    """Кэш пишется по ключу ai:cache:analyze:dashboard_overview:global."""
    from core.ai_assistant.chat import ChatResponse
    from core.ai_assistant.client import AIClient

    fake_client = AIClient(primary=None, fallback=None)
    fake_client._primary = object()
    monkeypatch.setattr("core.ai_assistant.client._client_singleton", fake_client)

    monkeypatch.setattr(
        "core.ai_assistant.chat.ChatSession.ask",
        AsyncMock(return_value=ChatResponse(answer="ok", tool_calls=[])),
    )

    app = _make_app(redis=fake_redis_client, settings=_settings_with_anthropic())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post(
            "/api/ai/analyze",
            json=_valid_body(block_type="dashboard_overview", scope_key="global"),
        )

    # Проверяем что ключ появился в Redis
    cached = await fake_redis_client.get("ai:cache:analyze:dashboard_overview:global")
    assert cached is not None
    data = json.loads(cached)
    assert data["block_type"] == "dashboard_overview"


# ─────────────────────── Провайдер в ответе ──────────────────────────────────


# Если настроен только OpenAI → model содержит openai-модель
@pytest.mark.asyncio
async def test_ai_analyze_provider_openai(fake_redis_client, monkeypatch) -> None:
    """Только OPENAI_API_KEY → поле model в ответе содержит openai-модель."""
    from core.ai_assistant.chat import ChatResponse
    from core.ai_assistant.client import AIClient

    settings = _settings_with_openai()

    fake_client = AIClient(primary=None, fallback=None)
    fake_client._fallback = object()  # имитируем openai-провайдер
    monkeypatch.setattr("core.ai_assistant.client._client_singleton", fake_client)

    monkeypatch.setattr(
        "core.ai_assistant.chat.ChatSession.ask",
        AsyncMock(return_value=ChatResponse(answer="openai answer", tool_calls=[])),
    )

    app = _make_app(redis=fake_redis_client, settings=settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/ai/analyze", json=_valid_body())

    assert resp.status_code == 200
    body = resp.json()
    # Без anthropic_api_key → model должна быть openai-моделью
    assert "openai" in body["model"].lower() or "gpt" in body["model"].lower()
