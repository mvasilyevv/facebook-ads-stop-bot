# -*- coding: utf-8 -*-
"""Регрессия pre-existing bug: get_recent_alerts SQL под схему.

До фикса: SQL ссылался на `ae.event_type`, `ae.rule_codes`, `a.name` —
несуществующие колонки. Tool падал с UndefinedColumnError при любом вызове
из /ask или MCP.

После фикса: используем `ae.stage`, `ae.matched_rule_codes`, `a.ad_name`.

Тестируем на реальной БД из docker-compose: INSERT в alert_events (partitioned
текущим месяцем) → вызов tool → строки без SQL-ошибки + корректное
форматирование.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.ai_assistant.tools.base import ToolContext
from core.ai_assistant.tools.ops.get_recent_alerts import GetRecentAlertsTool


@pytest_asyncio.fixture
async def alerts_world(pg_engine, fb_ad_fixture):
    """Заливает 3 alert_events: 2 warning и 1 stop. Чистит после теста."""
    ad_id = fb_ad_fixture.ad_id
    now = datetime.now(timezone.utc)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO alert_events
                    (ad_id, stage, state, matched_rule_codes,
                     metrics_json, created_at)
                VALUES
                    (:a, 'warning', 'warning_sent',
                     CAST('["spend_no_event"]' AS JSONB),
                     CAST('{"spend": 12.5}' AS JSONB), :t1),
                    (:a, 'stop', 'stop_sent',
                     CAST('["cpa", "frequency"]' AS JSONB),
                     CAST('{"spend": 30.0}' AS JSONB), :t2),
                    (:a, 'warning', 'warning_sent',
                     CAST('["ctr"]' AS JSONB), CAST('{}' AS JSONB), :t_old)
                """
            ),
            {
                "a": ad_id,
                "t1": now - timedelta(hours=2),
                "t2": now - timedelta(hours=4),
                "t_old": now - timedelta(hours=100),  # вне окна 24ч
            },
        )

    yield fb_ad_fixture

    # alert_events партиционирована: чистим по ad_id чтобы не мешать другим тестам.
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM alert_events WHERE ad_id = :a"), {"a": ad_id})


# SQL не падает на колонках (stage, matched_rule_codes, ad_name) — основной кейс.
@pytest.mark.asyncio
async def test_get_recent_alerts_sql_no_error(pg_engine, alerts_world) -> None:
    tool = GetRecentAlertsTool()
    ctx = ToolContext(client_key="test-user", engine=pg_engine)
    result = await tool.run(ctx, {"hours": 24, "limit": 50})

    # Заголовок: 2 алерта в окне (warning + stop), 1 старый отфильтрован.
    assert "Алертов за последние 24ч: 2" in result
    # Stage в верхнем регистре.
    assert "WARNING" in result
    assert "STOP" in result
    # Имя берётся из fb_ads.ad_name, не fb_ads.name.
    assert "AD_" in result  # fb_ad_fixture использует prefix AD_


# Фильтр по stage='stop' через ae.stage = :stg.
@pytest.mark.asyncio
async def test_get_recent_alerts_filter_by_stage(pg_engine, alerts_world) -> None:
    tool = GetRecentAlertsTool()
    ctx = ToolContext(client_key="test-user", engine=pg_engine)
    result = await tool.run(ctx, {"hours": 24, "stage": "stop"})

    assert "Алертов за последние 24ч: 1" in result
    assert "STOP" in result
    assert "WARNING" not in result


# matched_rule_codes отрисовывается как CSV-список.
@pytest.mark.asyncio
async def test_get_recent_alerts_renders_rule_codes(pg_engine, alerts_world) -> None:
    tool = GetRecentAlertsTool()
    ctx = ToolContext(client_key="test-user", engine=pg_engine)
    result = await tool.run(ctx, {"hours": 24, "stage": "stop"})

    # Один stop с правилами cpa+frequency.
    assert "cpa" in result and "frequency" in result


# Окно 1 час — оба свежих алерта пропустят (они старше 1ч), результат — пусто.
@pytest.mark.asyncio
async def test_get_recent_alerts_window_too_small(pg_engine, alerts_world) -> None:
    tool = GetRecentAlertsTool()
    ctx = ToolContext(client_key="test-user", engine=pg_engine)
    result = await tool.run(ctx, {"hours": 1})

    assert "Алертов за последние 1ч нет." in result
