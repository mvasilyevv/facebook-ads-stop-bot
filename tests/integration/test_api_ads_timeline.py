# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /ads/{fb_ad_id}/timeline.

Используем реальный Postgres из docker-compose и fakeredis.
Каждый тест изолирован: данные вставляются и удаляются через fixture.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(*, engine=None, redis=None):
    """Собирает FastAPI приложение с переопределёнными зависимостями."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def timeline_fixture(pg_engine):
    """Создаёт offer→campaign→adset→ad + метрики + alert_event + task_queue.

    Cleanup через CASCADE от offers.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    fb_ad_id = f"99000{suffix}"

    async with pg_engine.begin() as conn:
        # Базовая иерархия
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"TL_{suffix}", "n": f"TL offer {suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_TL_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_TL_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"AD_TL_{suffix}"},
        )
        # Метрика внутри стандартного 7-дневного окна
        await conn.execute(
            text(
                "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, impressions, clicks, leads, deposits) "
                "VALUES (gen_random_uuid(), :a, NOW() - INTERVAL '1 hour', :s, :imp, :cl, :l, :d)"
            ),
            {"a": ad_id, "s": Decimal("50.00"), "imp": 1000, "cl": 50, "l": 5, "d": 1},
        )
        # AlertEvent внутри окна
        await conn.execute(
            text(
                "INSERT INTO alert_events (id, ad_id, stage, state, matched_rule_codes, metrics_json, created_at) "
                "VALUES (gen_random_uuid(), :a, 'warning', 'warning_sent', :mc, '{}', NOW() - INTERVAL '2 hours')"
            ),
            {"a": ad_id, "mc": '["CPC"]'},
        )
        # TaskQueue c fb_ad_id в payload
        await conn.execute(
            text(
                "INSERT INTO task_queue (task_type, status, idempotency_key, payload, requested_by, created_at, updated_at) "
                "VALUES ('disable', 'succeeded', :ikey, :payload, 'bot_auto_stop', NOW() - INTERVAL '3 hours', NOW())"
            ),
            {
                "ikey": f"tl_test_{suffix}",
                "payload": f'{{"fb_ad_id": "{fb_ad_id}"}}',
            },
        )

    yield {
        "offer_id": offer_id,
        "fb_ad_id": fb_ad_id,
        "ad_id": ad_id,
        "suffix": suffix,
        "campaign_name": f"CMP_TL_{suffix}",
        "adset_name": f"ADSET_TL_{suffix}",
        "ad_name": f"AD_TL_{suffix}",
    }

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})


# Проверяем полный happy-путь: метрики + алерты + задачи возвращаются корректно.
@pytest.mark.asyncio
async def test_timeline_happy_path(pg_engine, fake_redis_client, timeline_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = timeline_fixture["fb_ad_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/ads/{fb_ad_id}/timeline")

    assert resp.status_code == 200
    data = resp.json()
    assert data["fb_ad_id"] == fb_ad_id
    assert data["ad_name"] == timeline_fixture["ad_name"]
    assert data["campaign_name"] == timeline_fixture["campaign_name"]
    assert data["adset_name"] == timeline_fixture["adset_name"]
    assert len(data["metrics"]) >= 1
    assert len(data["alerts"]) >= 1
    assert len(data["tasks"]) >= 1


# Несуществующий fb_ad_id должен вернуть 404.
@pytest.mark.asyncio
async def test_timeline_not_found(pg_engine, fake_redis_client):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/ads/nonexistent_99999/timeline")

    assert resp.status_code == 404


# По умолчанию окно последние 7 дней — данные за 1 час входят в него.
@pytest.mark.asyncio
async def test_timeline_default_window_includes_recent(
    pg_engine, fake_redis_client, timeline_fixture
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = timeline_fixture["fb_ad_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/ads/{fb_ad_id}/timeline")

    assert resp.status_code == 200
    data = resp.json()
    # Метрика за 1 час назад должна быть в дефолтном окне
    assert len(data["metrics"]) >= 1


# Кастомный from_iso/to_iso: запрос за будущий период даёт пустые данные.
@pytest.mark.asyncio
async def test_timeline_custom_window_future_empty(pg_engine, fake_redis_client, timeline_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = timeline_fixture["fb_ad_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/ads/{fb_ad_id}/timeline",
            params={
                "from_iso": "2030-01-01T00:00:00+00:00",
                "to_iso": "2030-01-02T00:00:00+00:00",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics"] == []
    assert data["alerts"] == []
    assert data["tasks"] == []


# include_metrics=false: метрики не возвращаются, алерты и задачи есть.
@pytest.mark.asyncio
async def test_timeline_exclude_metrics(pg_engine, fake_redis_client, timeline_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = timeline_fixture["fb_ad_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/ads/{fb_ad_id}/timeline", params={"include_metrics": "false"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics"] == []
    assert len(data["alerts"]) >= 1


# include_alerts=false: алерты пустые, метрики есть.
@pytest.mark.asyncio
async def test_timeline_exclude_alerts(pg_engine, fake_redis_client, timeline_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = timeline_fixture["fb_ad_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/ads/{fb_ad_id}/timeline", params={"include_alerts": "false"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["alerts"] == []
    assert len(data["metrics"]) >= 1


# include_tasks=false: задачи пустые, остальное есть.
@pytest.mark.asyncio
async def test_timeline_exclude_tasks(pg_engine, fake_redis_client, timeline_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = timeline_fixture["fb_ad_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/ads/{fb_ad_id}/timeline", params={"include_tasks": "false"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks"] == []
    assert len(data["metrics"]) >= 1


# AlertEvent использует поля stage и matched_rule_codes (не event_type/rule_codes).
@pytest.mark.asyncio
async def test_timeline_alert_fields_correct(pg_engine, fake_redis_client, timeline_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = timeline_fixture["fb_ad_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/ads/{fb_ad_id}/timeline")

    assert resp.status_code == 200
    alerts = resp.json()["alerts"]
    assert len(alerts) >= 1
    alert = alerts[0]
    # stage — актуальное поле, не event_type
    assert "stage" in alert
    assert alert["stage"] == "warning"
    # matched_rule_codes — актуальное поле, не rule_codes
    assert "matched_rule_codes" in alert
    assert "CPC" in alert["matched_rule_codes"]


# TaskQueue.status маппится в UPPERCASE через status_mapper.
@pytest.mark.asyncio
async def test_timeline_task_status_uppercase(pg_engine, fake_redis_client, timeline_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = timeline_fixture["fb_ad_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/ads/{fb_ad_id}/timeline")

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) >= 1
    # В БД status='succeeded', фронт должен получить 'SUCCEEDED'
    assert tasks[0]["status"] == "SUCCEEDED"


# Sanity: 100 метрик вставляются и запрос выполняется за < 500ms (partition pruning работает).
@pytest.mark.asyncio
async def test_timeline_100_metrics_performance(pg_engine, fake_redis_client):
    """Партиционный запрос по cycle_ts должен работать без full scan."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    fb_ad_id = f"88000{suffix}"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"PERF_{suffix}", "n": "Perf test offer"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_P_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADS_P_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"AD_P_{suffix}"},
        )
        # Вставляем 100 метрик за разные часы в пределах 7 дней
        for i in range(100):
            await conn.execute(
                text(
                    "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend) "
                    f"VALUES (gen_random_uuid(), :a, NOW() - INTERVAL '{i + 1} hours', :s)"
                ),
                {"a": ad_id, "s": Decimal(f"{10 + i}.00")},
            )

    app = _make_app(engine=pg_engine, redis=None)

    start = time.monotonic()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/ads/{fb_ad_id}/timeline")
    elapsed = time.monotonic() - start

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["metrics"]) == 100
    assert elapsed < 0.5, f"Запрос занял {elapsed:.3f}s, ожидалось < 0.5s"

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})
