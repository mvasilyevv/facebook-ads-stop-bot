# -*- coding: utf-8 -*-
"""Opt-in live тесты против настоящего AdSet.pro MCP-сервера.

Помечены @pytest.mark.live — по умолчанию исключаются (addopts="-m 'not live'"
в pyproject.toml). Запуск:

    pytest -m live --override-ini="addopts=" tests/integration/test_adset_pro_live.py

Требуется .env с ADSETPRO_MCP_KEY и реальный HTTPS-доступ к https://adset.pro.
Live verify 2026-05-27 подтвердил: сервер `platform-stats-mcp` v1.0.0, JSON-RPC
2.0 (MCP 2025-06-18). Если AdSet.pro поменяет схему / отзовёт ключ — тесты упадут
именно здесь, а не в мок-юнитах.
"""

from __future__ import annotations

from datetime import date

import pytest

from core.adset_pro import AdsetProClient, StatsQueryRequest
from core.config import get_settings, reveal_secret


def _skip_if_no_real_key() -> None:
    """Если MCP-ключа нет в .env / окружении — тест пропускается."""
    settings = get_settings()
    if not settings.adsetpro_mcp_key or not reveal_secret(settings.adsetpro_mcp_key).startswith(
        "mcp_"
    ):
        pytest.skip("ADSETPRO_MCP_KEY не задан или не начинается с mcp_ — пропускаем live тест")


# Live: health_check должен пройти через JSON-RPC initialize.
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_health_check_ok() -> None:
    _skip_if_no_real_key()
    async with AdsetProClient() as client:
        ok = await client.health_check()
    assert ok is True, "AdSet.pro initialize не вернул валидный result"


# Live: call_mcp_tool("get_metadata", {"type":"metrics"}) — публичная безопасная операция,
# не зависит от данных в кабинете.
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_get_metadata_returns_metrics_list() -> None:
    _skip_if_no_real_key()
    async with AdsetProClient() as client:
        data = await client.call_mcp_tool("get_metadata", {"type": "metrics"})
    assert isinstance(data, dict)
    assert "metrics" in data, f"Ожидали ключ metrics, получили {list(data.keys())}"
    assert isinstance(data["metrics"], list)
    # Хотя бы один metric c обязательными полями key/type/title.
    if data["metrics"]:
        first = data["metrics"][0]
        assert {"key", "type", "title"} <= set(first.keys())


# Live: query_stats за сегодня — может вернуть пустой data, главное что не падает на схеме.
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_query_stats_today_does_not_error() -> None:
    _skip_if_no_real_key()
    today = date.today()
    async with AdsetProClient() as client:
        response = await client.query_stats(StatsQueryRequest(since=today, until=today))
    # Не проверяем содержимое — может быть пусто в кабинете без трафика.
    assert response.rows is not None
