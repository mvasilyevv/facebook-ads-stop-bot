# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /api/dashboard/enable-tasks.

Проверяет что возвращаются только enable-задачи, маппинг статусов корректен,
фильтрация по статусу и fb_ad_id работает, ad_name подхватывается через JOIN.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(engine=None, redis=None):
    """Собираем FastAPI с переопределёнными deps."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_enable_tasks(pg_engine):
    """Очищает тестовые задачи и ads до и после теста."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE idempotency_key LIKE 'enable:99ETST%'")
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE '99ETST%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'ETST_%'"))
            await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'ETST_%'"))
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'ETST_%'"))

    await _cleanup()
    yield
    await _cleanup()


async def _seed_ad(conn, suffix: str) -> tuple[str, uuid.UUID]:
    """Создаёт offer→campaign→adset→ad. Возвращает (fb_ad_id, ad_internal_id)."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"99ETST{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"ETST_{suffix}", "n": f"ETest offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"ETST_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"ETST_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"ETest AD {suffix}"},
    )
    return fb_ad_id, ad_id


async def _insert_task(conn, fb_ad_id: str, task_type: str, status: str, suffix: str) -> int:
    """Вставляет задачу указанного типа. Возвращает id."""
    ikey = f"{task_type}:{fb_ad_id}:test:{suffix}"
    result = await conn.execute(
        text(
            """
            INSERT INTO task_queue
                (task_type, status, idempotency_key, payload,
                 attempt_count, max_attempts, requested_by)
            VALUES
                (:tt, :st, :ik, CAST(:pl AS JSONB), 0, 5, 'test_user')
            RETURNING id
            """
        ),
        {"tt": task_type, "st": status, "ik": ikey, "pl": f'{{"fb_ad_id": "{fb_ad_id}"}}'},
    )
    return result.first()[0]


# ─── Тест 1 ──────────────────────────────────────────────────────────────────
# Проверяем что disable-задачи не попадают в enable endpoint
@pytest.mark.asyncio
async def test_enable_tasks_only_enable_type(
    pg_engine, fake_redis_client, clean_enable_tasks
) -> None:
    """GET enable-tasks → только task_type='enable', disable не возвращаются."""
    suffix = uuid.uuid4().hex[:6]

    async with pg_engine.begin() as conn:
        fb_ad_id, _ = await _seed_ad(conn, suffix)
        await _insert_task(conn, fb_ad_id, "enable", "pending", f"e_{suffix}")
        await _insert_task(conn, fb_ad_id, "disable", "pending", f"d_{suffix}")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/enable-tasks")

    assert resp.status_code == 200
    items = resp.json()
    # enable-endpoint возвращает только enable-канал: legacy task_type='enable' +
    # meta_api_mutation activate_ad. disable/pause-задачи исключены SQL-фильтром
    # (enable_channel_sql). Наша enable-задача присутствует, disable — нет.
    assert all(it["task_type"] in ("enable", "meta_api_mutation") for it in items)
    assert any(it["task_type"] == "enable" for it in items)


# ─── Тест 2 ──────────────────────────────────────────────────────────────────
# Проверяем корректность маппинга статусов draft/pending → PENDING
@pytest.mark.asyncio
async def test_enable_tasks_status_mapping(
    pg_engine, fake_redis_client, clean_enable_tasks
) -> None:
    """Статусы draft/pending → PENDING, failed → FAILED в ответе."""
    suffix = uuid.uuid4().hex[:6]

    async with pg_engine.begin() as conn:
        fb_ad_id, _ = await _seed_ad(conn, suffix)
        await _insert_task(conn, fb_ad_id, "enable", "draft", f"dr_{suffix}")
        await _insert_task(conn, fb_ad_id, "enable", "pending", f"pe_{suffix}")
        await _insert_task(conn, fb_ad_id, "enable", "failed", f"fa_{suffix}")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/enable-tasks")

    assert resp.status_code == 200
    items = resp.json()
    returned_statuses = {item["status"] for item in items}
    assert "PENDING" in returned_statuses
    assert "FAILED" in returned_statuses
    assert "draft" not in returned_statuses
    assert "pending" not in returned_statuses


# ─── Тест 3 ──────────────────────────────────────────────────────────────────
# Проверяем фильтрацию по статусу RUNNING
@pytest.mark.asyncio
async def test_enable_tasks_filter_by_status(
    pg_engine, fake_redis_client, clean_enable_tasks
) -> None:
    """?status=RUNNING → только running задачи."""
    suffix = uuid.uuid4().hex[:6]

    async with pg_engine.begin() as conn:
        fb_ad_id, _ = await _seed_ad(conn, suffix)
        await _insert_task(conn, fb_ad_id, "enable", "running", f"ru_{suffix}")
        await _insert_task(conn, fb_ad_id, "enable", "pending", f"pe_{suffix}")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/enable-tasks?status=RUNNING")

    assert resp.status_code == 200
    items = resp.json()
    assert all(item["status"] == "RUNNING" for item in items)


# ─── Тест 4 ──────────────────────────────────────────────────────────────────
# Проверяем что ad_name подхватывается через LEFT JOIN fb_ads
@pytest.mark.asyncio
async def test_enable_tasks_ad_name_via_join(
    pg_engine, fake_redis_client, clean_enable_tasks
) -> None:
    """ad_name приходит из fb_ads через JOIN по payload->>'fb_ad_id'."""
    suffix = uuid.uuid4().hex[:6]

    async with pg_engine.begin() as conn:
        fb_ad_id, _ = await _seed_ad(conn, suffix)
        await _insert_task(conn, fb_ad_id, "enable", "pending", suffix)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/dashboard/enable-tasks?fb_ad_id={fb_ad_id}")

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    assert items[0]["ad_name"] == f"ETest AD {suffix}"
