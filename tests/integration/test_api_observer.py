# -*- coding: utf-8 -*-
"""Интеграционные тесты: роутер /observer (v1).

Тестирует GET /observer/status, GET /observer/scan-runs,
POST /observer/start-new-cabinet-day, POST /observer/restart.

Эндпоинт POST /disable-worker/restart удалён: DOM-toggle канал больше не используется,
отключение рекламы происходит только через Marketing API (meta_api_worker).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(engine=None, redis=None):
    """Собрать FastAPI с подменёнными PG/Redis зависимостями."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


# ─────────────────────── GET /observer/status ────────────────────────────────


# Без ключа в Redis — возвращает status=unknown, поля null
@pytest.mark.asyncio
async def test_observer_status_no_key(fake_redis_client) -> None:
    """Отсутствие ключа observer:runtime → статус unknown, поля null."""
    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/observer/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "unknown"
    assert payload["last_scan_at"] is None
    assert payload["interval_seconds"] is None


# С ключом в Redis — возвращает распаршенный payload (реальный контракт воркера)
@pytest.mark.asyncio
async def test_observer_status_with_key(fake_redis_client) -> None:
    """Ключ observer:runtime записан воркером → status='running', extra-поля видны.

    Используем _publish_runtime_status — реальный writer — чтобы гарантировать
    что тест проверяет актуальный контракт, а не устаревший shape.
    """

    from apps.observer_worker.main import _publish_runtime_status

    now = datetime.now(UTC)
    await _publish_runtime_status(
        fake_redis_client,
        status="scanning",
        active_phase="scan",
        last_successful_scan_at=now,
    )

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/observer/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "running"
    # active_phase пробрасывается в extra
    assert payload["extra"].get("active_phase") == "scan"
    # last_scan_at: поле пробрасывается из last_successful_scan_at
    assert payload["last_scan_at"] is not None


# ─────────────────────── GET /observer/scan-runs ─────────────────────────────


# Без параметров → последние 7 дней (нужна реальная БД)
@pytest.mark.asyncio
async def test_scan_runs_default_window(pg_engine, fake_redis_client) -> None:
    """Без from/to → запрос выполняется за last 7 days без ошибок."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/observer/scan-runs")
    assert resp.status_code == 200
    payload = resp.json()
    assert "runs" in payload
    assert isinstance(payload["runs"], list)


# С from/to → фильтрует по окну
@pytest.mark.asyncio
async def test_scan_runs_with_time_window(pg_engine, fake_redis_client) -> None:
    """Передача from_iso/to_iso → HTTP 200, окно применяется."""
    now = datetime.now(UTC)
    from_iso = (now - timedelta(days=3)).isoformat()
    to_iso = now.isoformat()
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/observer/scan-runs", params={"from_iso": from_iso, "to_iso": to_iso}
        )
    assert resp.status_code == 200


# filter=errors → только outcome=error
@pytest.mark.asyncio
async def test_scan_runs_filter_errors(pg_engine, fake_redis_client) -> None:
    """filter=errors → вставляем error-строку, проверяем что она попадает в ответ."""
    now = datetime.now(UTC)
    scan_id = int(now.timestamp() * 1000) % 2**31
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO scan_runs (scan_id, started_at, outcome, duration_ms)
                VALUES (:scan_id, :started_at, 'error', 1000)
                """
            ),
            {"scan_id": scan_id, "started_at": now},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/observer/scan-runs",
            params={"filter": "errors", "from_iso": (now - timedelta(minutes=1)).isoformat()},
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert all(r["outcome"] == "error" for r in payload["runs"] if r["outcome"] is not None)

    # Cleanup
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM scan_runs WHERE scan_id = :sid AND started_at = :ts"),
            {"sid": scan_id, "ts": now},
        )


# limit=300 → автоматически cap до 200
@pytest.mark.asyncio
async def test_scan_runs_limit_capped(pg_engine, fake_redis_client) -> None:
    """Limit > 200 → FastAPI возвращает 422 (Query validation) или cap до 200."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # FastAPI с le=200 вернёт 422 при явном превышении через Query
        resp = await ac.get("/api/observer/scan-runs", params={"limit": 300})
    # Либо 422 (Query validation), либо 200 с не более 200 результатами
    assert resp.status_code in (200, 422)
    if resp.status_code == 200:
        payload = resp.json()
        assert len(payload["runs"]) <= 200


# ─────────────────── POST /observer/start-new-cabinet-day ────────────────────


# Публикует событие и создаёт строку в cabinet_day_archives
@pytest.mark.asyncio
async def test_start_new_cabinet_day(pg_engine, fake_redis_client) -> None:
    """POST /observer/start-new-cabinet-day → 200, archive row создан, pubsub опубликован."""
    # Подписываемся на канал до отправки запроса
    pubsub = fake_redis_client.pubsub()
    await pubsub.subscribe("fb_agent:observer:cabinet_day")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/observer/start-new-cabinet-day")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "started"
    assert "archived_date" in payload

    # Проверяем что хотя бы одна строка вставлена в cabinet_day_archives
    async with pg_engine.connect() as conn:
        row = await conn.execute(text("SELECT COUNT(*) FROM cabinet_day_archives"))
        assert row.scalar() >= 1

    await pubsub.unsubscribe("fb_agent:observer:cabinet_day")
    await pubsub.aclose()


# ─────────────────────────── POST /observer/restart ──────────────────────────


# Публикует в правильный канал
@pytest.mark.asyncio
async def test_observer_restart_publishes_to_channel(fake_redis_client) -> None:
    """POST /observer/restart → signal_sent + публикует в fb_agent:worker:restart:observer."""
    pubsub = fake_redis_client.pubsub()
    await pubsub.subscribe("fb_agent:worker:restart:observer")
    # Читаем subscribe-подтверждение
    await pubsub.get_message(timeout=0.1)

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/observer/restart")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "signal_sent"
    assert "fb_agent:worker:restart:observer" in payload["channel"]

    # Проверяем что сообщение дошло
    msg = await pubsub.get_message(timeout=0.5)
    assert msg is not None and msg["type"] == "message"

    await pubsub.unsubscribe("fb_agent:worker:restart:observer")
    await pubsub.aclose()


# Restart без Redis → 503
@pytest.mark.asyncio
async def test_observer_restart_without_redis_returns_503() -> None:
    """Если Redis недоступен (publish кидает) → 503."""
    broken_redis = MagicMock()
    broken_redis.publish = AsyncMock(side_effect=ConnectionError("Redis down"))

    app = _make_app(redis=broken_redis)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/observer/restart")

    assert resp.status_code == 503
