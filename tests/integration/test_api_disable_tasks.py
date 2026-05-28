# -*- coding: utf-8 -*-
"""Интеграционные тесты GET/POST/retry/cancel /api/dashboard/disable-tasks.

Требуется реальный Postgres v2 из docker-compose. Каждый тест очищает
task_queue + fb_ads (через offers cascade) в teardown.
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
    """Создаём FastAPI с подменёнными зависимостями для теста."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_tasks(pg_engine):
    """Очищает task_queue и тестовые offers/fb_ads до и после теста."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            # Удаляем все disable-задачи с тестовыми ключами (DTST-суффикс)
            await conn.execute(
                text(
                    "DELETE FROM task_queue WHERE task_type = 'disable' AND idempotency_key LIKE '%DTST%'"
                )
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE '99DTST%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'DTST_%'"))
            await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'DTST_%'"))
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'DTST_%'"))

    await _cleanup()
    yield
    await _cleanup()


async def _seed_ad(conn, suffix: str) -> str:
    """Создаёт offer→campaign→adset→ad. Возвращает fb_ad_id."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"99DTST{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"DTST_{suffix}", "n": f"DTest offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"DTST_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"DTST_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"DTest AD {suffix}"},
    )
    return fb_ad_id


async def _insert_task(conn, fb_ad_id: str, status: str, suffix: str) -> int:
    """Вставляет disable-задачу напрямую. Возвращает task id."""
    ikey = f"disable:{fb_ad_id}:test:{suffix}"
    result = await conn.execute(
        text(
            """
            INSERT INTO task_queue
                (task_type, status, idempotency_key, payload,
                 attempt_count, max_attempts, requested_by)
            VALUES
                ('disable', :st, :ik, CAST(:pl AS JSONB), 0, 5, 'test_user')
            RETURNING id
            """
        ),
        {"st": status, "ik": ikey, "pl": f'{{"fb_ad_id": "{fb_ad_id}"}}'},
    )
    return result.first()[0]


# ─── Тест 1 ──────────────────────────────────────────────────────────────────
# Проверяем что фильтр по несуществующему fb_ad_id возвращает пустой список
@pytest.mark.asyncio
async def test_list_disable_tasks_empty(pg_engine, fake_redis_client, clean_tasks) -> None:
    """GET с фильтром по несуществующему fb_ad_id → пустой список []."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Используем заведомо несуществующий fb_ad_id вместо глобальной пустой проверки
        resp = await ac.get("/api/dashboard/disable-tasks?fb_ad_id=000000000000001")

    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["x-total-count"] == "0"


# ─── Тест 2 ──────────────────────────────────────────────────────────────────
# Проверяем правильное uppercase-маппирование всех статусов включая draft→PENDING
@pytest.mark.asyncio
async def test_list_disable_tasks_status_mapping(pg_engine, fake_redis_client, clean_tasks) -> None:
    """5 задач с разными статусами → uppercase mapping корректен (draft/pending → PENDING)."""
    suffix = uuid.uuid4().hex[:6]
    statuses = ["draft", "pending", "running", "failed", "succeeded"]

    async with pg_engine.begin() as conn:
        fb_ad_id = await _seed_ad(conn, suffix)
        for i, st in enumerate(statuses):
            await _insert_task(conn, fb_ad_id, st, f"{suffix}_{i}")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/disable-tasks")

    assert resp.status_code == 200
    items = resp.json()
    returned_statuses = {item["status"] for item in items}

    # draft и pending → оба PENDING
    assert "PENDING" in returned_statuses
    assert "draft" not in returned_statuses
    assert "pending" not in returned_statuses
    # остальные — uppercase
    assert "RUNNING" in returned_statuses
    assert "FAILED" in returned_statuses
    assert "SUCCEEDED" in returned_statuses


# ─── Тест 3 ──────────────────────────────────────────────────────────────────
# Проверяем что фильтр по статусу возвращает только нужные задачи
@pytest.mark.asyncio
async def test_list_disable_tasks_filter_by_status(
    pg_engine, fake_redis_client, clean_tasks
) -> None:
    """?status=FAILED → только задачи со статусом failed."""
    suffix = uuid.uuid4().hex[:6]

    async with pg_engine.begin() as conn:
        fb_ad_id = await _seed_ad(conn, suffix)
        await _insert_task(conn, fb_ad_id, "failed", f"{suffix}_0")
        await _insert_task(conn, fb_ad_id, "pending", f"{suffix}_1")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/disable-tasks?status=FAILED")

    assert resp.status_code == 200
    items = resp.json()
    assert all(item["status"] == "FAILED" for item in items)
    assert len(items) >= 1


# ─── Тест 4 ──────────────────────────────────────────────────────────────────
# Проверяем фильтр по fb_ad_id
@pytest.mark.asyncio
async def test_list_disable_tasks_filter_by_fb_ad_id(
    pg_engine, fake_redis_client, clean_tasks
) -> None:
    """?fb_ad_id=X → только задачи для этого объявления."""
    suffix_a = uuid.uuid4().hex[:6]
    suffix_b = uuid.uuid4().hex[:6]

    async with pg_engine.begin() as conn:
        fb_ad_id_a = await _seed_ad(conn, suffix_a)
        fb_ad_id_b = await _seed_ad(conn, suffix_b)
        await _insert_task(conn, fb_ad_id_a, "pending", suffix_a)
        await _insert_task(conn, fb_ad_id_b, "pending", suffix_b)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/dashboard/disable-tasks?fb_ad_id={fb_ad_id_a}")

    assert resp.status_code == 200
    items = resp.json()
    assert all(item["fb_ad_id"] == fb_ad_id_a for item in items)


# ─── Тест 5 ──────────────────────────────────────────────────────────────────
# Проверяем успешное создание задачи через POST
@pytest.mark.asyncio
async def test_create_disable_task_happy(pg_engine, fake_redis_client, clean_tasks) -> None:
    """POST happy path → 201, задача в БД с task_type='disable'."""
    suffix = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fb_ad_id = await _seed_ad(conn, suffix)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/disable-tasks",
            json={"fb_ad_id": fb_ad_id, "requested_by": "tester", "reason": "test disable"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["task_type"] == "disable"
    assert data["fb_ad_id"] == fb_ad_id
    assert data["status"] == "PENDING"
    assert data["requested_by"] == "tester"

    # Проверяем что задача реально есть в БД
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT task_type, status FROM task_queue WHERE id = :tid"),
                {"tid": int(data["id"])},
            )
        ).first()
    assert row is not None
    assert row.task_type == "disable"
    assert row.status == "pending"


# ─── Тест 6 ──────────────────────────────────────────────────────────────────
# Проверяем 404 для несуществующего fb_ad_id
@pytest.mark.asyncio
async def test_create_disable_task_unknown_fb_ad_id(
    pg_engine, fake_redis_client, clean_tasks
) -> None:
    """POST для несуществующего fb_ad_id → 404."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/disable-tasks",
            json={"fb_ad_id": "000000000000000", "requested_by": "tester"},
        )

    assert resp.status_code == 404


# ─── Тест 7 ──────────────────────────────────────────────────────────────────
# Проверяем retry задачи в статусе failed
@pytest.mark.asyncio
async def test_retry_disable_task_happy(pg_engine, fake_redis_client, clean_tasks) -> None:
    """POST /retry для задачи status='failed' → 200, статус='RETRYING'."""
    suffix = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fb_ad_id = await _seed_ad(conn, suffix)
        task_id = await _insert_task(conn, fb_ad_id, "failed", suffix)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/api/dashboard/disable-tasks/{task_id}/retry")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "RETRYING"


# ─── Тест 8 ──────────────────────────────────────────────────────────────────
# Проверяем что retry для running-задачи запрещён
@pytest.mark.asyncio
async def test_retry_disable_task_running_conflict(
    pg_engine, fake_redis_client, clean_tasks
) -> None:
    """POST /retry для задачи status='running' → 409 (нельзя retry активную)."""
    suffix = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fb_ad_id = await _seed_ad(conn, suffix)
        task_id = await _insert_task(conn, fb_ad_id, "running", suffix)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/api/dashboard/disable-tasks/{task_id}/retry")

    assert resp.status_code == 409


# ─── Тест 9 ──────────────────────────────────────────────────────────────────
# Проверяем успешную отмену pending-задачи
@pytest.mark.asyncio
async def test_cancel_disable_task_happy(pg_engine, fake_redis_client, clean_tasks) -> None:
    """DELETE для pending-задачи → 204, status в БД = 'cancelled'."""
    suffix = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fb_ad_id = await _seed_ad(conn, suffix)
        task_id = await _insert_task(conn, fb_ad_id, "pending", suffix)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(f"/api/dashboard/disable-tasks/{task_id}")

    assert resp.status_code == 204

    # Убеждаемся что статус изменился
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :tid"), {"tid": task_id}
            )
        ).first()
    assert row.status == "cancelled"


# ─── Тест 10 ─────────────────────────────────────────────────────────────────
# Проверяем что отмена succeeded-задачи запрещена
@pytest.mark.asyncio
async def test_cancel_disable_task_succeeded_conflict(
    pg_engine, fake_redis_client, clean_tasks
) -> None:
    """DELETE для succeeded-задачи → 409 (терминальный статус, отмена запрещена)."""
    suffix = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fb_ad_id = await _seed_ad(conn, suffix)
        task_id = await _insert_task(conn, fb_ad_id, "succeeded", suffix)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(f"/api/dashboard/disable-tasks/{task_id}")

    assert resp.status_code == 409
