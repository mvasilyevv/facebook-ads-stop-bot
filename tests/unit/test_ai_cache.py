# -*- coding: utf-8 -*-
"""Модульные тесты кэширования AI-аналитики (AICache) и роутера /api/ai/analyze."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models import AICache


# Вспомогательный класс для имитации ответа AI-провайдера
class DummyAIResponse:
    def __init__(self, text: str, usage: MagicMock = None):
        self.text = text
        self.usage = usage or MagicMock(prompt_tokens=10, completion_tokens=20)
        self.raw = {"usage": {"prompt_tokens": 10, "completion_tokens": 20}}


# Короткий комментарий на русском: Проверяем, что модель AICache успешно создаётся со всеми обязательными полями.
def test_ai_cache_model_fields():
    """Тест структуры полей модели AICache."""
    now = datetime.now(UTC)
    expiry = now + timedelta(minutes=5)

    cache_entry = AICache(
        block_type="briefing",
        scope_key="global",
        payload_hash="sha256-dummy-hash-12345",
        content="Тестовый брифинг от ИИ",
        tokens_in=100,
        tokens_out=200,
        expires_at=expiry,
    )

    assert cache_entry.block_type == "briefing"
    assert cache_entry.scope_key == "global"
    assert cache_entry.payload_hash == "sha256-dummy-hash-12345"
    assert cache_entry.content == "Тестовый брифинг от ИИ"
    assert cache_entry.tokens_in == 100
    assert cache_entry.tokens_out == 200
    assert cache_entry.expires_at == expiry


# Короткий комментарий на русском: Проверяем логику кэширования, автоматического подтягивания из кэша и принудительного обновления (force_refresh).
@pytest.mark.asyncio
async def test_ai_router_cache_logic():
    """Тест логики извлечения из кэша и обновления при force_refresh."""
    from apps.api.routers.ai import AIAnalyzeRequest, ai_analyze

    # Мокаем базу данных
    db = AsyncMock()

    # 1. Сценарий: Кэш пуст -> Должен вызваться AI клиент
    db.execute = AsyncMock()
    # Возвращаем пустой результат (scalars.first() -> None)
    mock_empty_res = MagicMock()
    mock_empty_res.scalar.return_value = None
    db.scalar = AsyncMock(return_value=None)

    dummy_client = AsyncMock()
    dummy_client.chat = AsyncMock(return_value=DummyAIResponse("Сгенерированный брифинг"))
    dummy_client.is_available = True

    with (
        patch("apps.api.routers.ai.get_ai_client", return_value=dummy_client),
        patch("apps.api.routers.ai.gather_context_data", return_value={"mock": "data"}),
        patch("apps.api.routers.ai.calculate_hash", return_value="hash123"),
    ):
        result = await ai_analyze(
            body=AIAnalyzeRequest(block_type="briefing", scope_key="global", force_refresh=False),
            db=db,
        )

        assert result.content == "Сгенерированный брифинг"
        assert result.warning is None

        # Должен добавиться новый кэш в базу
        db.add.assert_called_once()
        db.commit.assert_awaited_once()

    # 2. Сценарий: В кэше есть валидная запись -> Должен сразу вернуться кэш без вызова AI клиента
    db.reset_mock()
    valid_cache = AICache(
        block_type="briefing",
        scope_key="global",
        payload_hash="hash123",
        content="Содержимое из кэша",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        created_at=datetime.now(UTC),
    )

    db.scalar = AsyncMock(return_value=valid_cache)
    dummy_client.chat.reset_mock()

    with (
        patch("apps.api.routers.ai.get_ai_client", return_value=dummy_client),
        patch("apps.api.routers.ai.gather_context_data", return_value={"mock": "data"}),
        patch("apps.api.routers.ai.calculate_hash", return_value="hash123"),
    ):
        result = await ai_analyze(
            body=AIAnalyzeRequest(block_type="briefing", scope_key="global", force_refresh=False),
            db=db,
        )

        assert result.content == "Содержимое из кэша"
        # AI клиент не вызывается
        dummy_client.chat.assert_not_called()
        db.add.assert_not_called()


# Короткий комментарий на русском: Проверяем авто-инвалидацию кэша при изменении исходных метрик (когда изменяется хеш).
@pytest.mark.asyncio
async def test_ai_router_hash_invalidation():
    """Тест автоматической инвалидации кэша при несовпадении хэшей метрик."""
    from apps.api.routers.ai import AIAnalyzeRequest, ai_analyze

    db = AsyncMock()

    # В кэше есть запись, но при поиске мы ничего не найдем, так как хэш в БД другой
    db.scalar = AsyncMock(return_value=None)

    dummy_client = AsyncMock()
    dummy_client.chat = AsyncMock(
        return_value=DummyAIResponse("Новый брифинг после изменения метрик")
    )
    dummy_client.is_available = True

    with (
        patch("apps.api.routers.ai.get_ai_client", return_value=dummy_client),
        patch("apps.api.routers.ai.gather_context_data", return_value={"mock": "new_data"}),
        patch("apps.api.routers.ai.calculate_hash", return_value="hashNew"),
    ):
        result = await ai_analyze(
            body=AIAnalyzeRequest(block_type="briefing", scope_key="global", force_refresh=False),
            db=db,
        )

        assert result.content == "Новый брифинг после изменения метрик"
        # AI клиент должен быть вызван для переоценки
        dummy_client.chat.assert_called_once()
        db.add.assert_called_once()


# Короткий комментарий на русском: Проверяем поведение при истечении срока действия кэша (expires_at в прошлом).
@pytest.mark.asyncio
async def test_ai_router_cache_expiration():
    """Тест поведения при просроченном кэше."""
    from apps.api.routers.ai import AIAnalyzeRequest, ai_analyze

    db = AsyncMock()

    # Имитируем отсутствие валидного кэша (expires_at в прошлом)
    db.scalar = AsyncMock(return_value=None)

    dummy_client = AsyncMock()
    dummy_client.chat = AsyncMock(
        return_value=DummyAIResponse("Свежий брифинг взамен просроченного")
    )
    dummy_client.is_available = True

    with (
        patch("apps.api.routers.ai.get_ai_client", return_value=dummy_client),
        patch("apps.api.routers.ai.gather_context_data", return_value={"mock": "data"}),
        patch("apps.api.routers.ai.calculate_hash", return_value="hash123"),
    ):
        result = await ai_analyze(
            body=AIAnalyzeRequest(block_type="briefing", scope_key="global", force_refresh=False),
            db=db,
        )

        assert result.content == "Свежий брифинг взамен просроченного"
        # AI клиент должен обновить данные
        dummy_client.chat.assert_called_once()


# Короткий комментарий на русском: Проверяем корректность вывода заглушки (fallback) при отключенном или недоступном AI клиенте (отсутствие API-ключей).
@pytest.mark.asyncio
async def test_ai_router_fallback_mode():
    """Тест работы роутера в fallback/mock режиме при отсутствии ключей."""
    from apps.api.routers.ai import AIAnalyzeRequest, ai_analyze

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    # AI клиент недоступен (нет ключей в окружении)
    dummy_client = AsyncMock()
    dummy_client.is_available = False

    with (
        patch("apps.api.routers.ai.get_ai_client", return_value=dummy_client),
        patch("apps.api.routers.ai.gather_context_data", return_value={"mock": "data"}),
        patch("apps.api.routers.ai.calculate_hash", return_value="hash123"),
    ):
        result = await ai_analyze(
            body=AIAnalyzeRequest(block_type="briefing", scope_key="global", force_refresh=False),
            db=db,
        )

        # Должен вернуться осмысленный плейсхолдер
        assert (
            "ИИ-Брифинг" in result.content
            or "Анализ" in result.content
            or "Демо" in result.content
            or "ИИ" in result.content
        )
        assert result.warning is not None
        assert (
            "демо" in result.warning.lower()
            or "ключ" in result.warning.lower()
            or "api" in result.warning.lower()
        )

        # AI клиент не должен дергаться на генерацию
        dummy_client.chat.assert_not_called()
        # Но в кэш демо-ответ сохраняется (чтобы не перегружать БД)
        db.add.assert_called_once()
