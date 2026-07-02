# -*- coding: utf-8 -*-
"""Интеграционные тесты POST /api/dashboard/ads/bulk-delete (hard-delete + orphan-задачи).

Требуется реальный Postgres из docker-compose. R1 (CRIT): при удалении ad из fb_ads
надо в той же транзакции отменить active-задачи в task_queue (outbox без FK), иначе
meta_api_worker исполнит orphan pause_ad/activate_ad вслепую по target_id удалённого ада.
Каждый тест очищает task_queue + fb_ads/offers в teardown.
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
async def clean_bd(pg_engine):
    """Очищает task_queue и тестовые offers/fb_ads до и после теста (prefix BDST)."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM task_queue WHERE idempotency_key LIKE '%BDST%' "
                    "OR payload->>'target_id' LIKE '99BDST%' "
                    "OR payload->>'fb_ad_id' LIKE '99BDST%'"
                )
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE '99BDST%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'BDST_%'"))
            await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'BDST_%'"))
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'BDST_%'"))

    await _cleanup()
    yield
    await _cleanup()


async def _seed_ad(conn, suffix: str) -> str:
    """Создаёт offer→campaign→adset→ad. Возвращает fb_ad_id."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"99BDST{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"BDST_{suffix}", "n": f"BDtest offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"BDST_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"BDST_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"BDtest AD {suffix}"},
    )
    return fb_ad_id


async def _insert_meta_task(conn, mutation_kind: str, target_id: str, status: str, suffix: str):
    """Вставляет meta_api_mutation-задачу (pause_ad/activate_ad) по target_id."""
    ikey = f"meta:{mutation_kind}:{target_id}:BDST:{suffix}"
    payload = f'{{"mutation_kind": "{mutation_kind}", "target_id": "{target_id}"}}'
    result = await conn.execute(
        text(
            """
            INSERT INTO task_queue
                (task_type, status, idempotency_key, payload,
                 attempt_count, max_attempts, requested_by)
            VALUES
                ('meta_api_mutation', :st, :ik, CAST(:pl AS JSONB), 0, 5, 'test_user')
            RETURNING id
            """
        ),
        {"st": status, "ik": ikey, "pl": payload},
    )
    return result.first()[0]


async def _insert_bulk_task(conn, ad_ids: list[str], status: str, suffix: str):
    """Вставляет bulk_status_change-задачу с params.ad_ids."""
    ikey = f"meta:bulk_status_change:BDST:{suffix}"
    ids_json = ", ".join(f'"{i}"' for i in ad_ids)
    payload = (
        '{"mutation_kind": "bulk_status_change", "target_id": "bulk", '
        f'"params": {{"ad_ids": [{ids_json}], "action": "activate"}}}}'
    )
    result = await conn.execute(
        text(
            """
            INSERT INTO task_queue
                (task_type, status, idempotency_key, payload,
                 attempt_count, max_attempts, requested_by)
            VALUES
                ('meta_api_mutation', :st, :ik, CAST(:pl AS JSONB), 0, 5, 'test_user')
            RETURNING id
            """
        ),
        {"st": status, "ik": ikey, "pl": payload},
    )
    return result.first()[0]


async def _task_status(conn, task_id: int) -> str:
    row = await conn.execute(
        text("SELECT status FROM task_queue WHERE id = :id"), {"id": task_id}
    )
    return row.first()[0]


# ─── Тест 1 ──────────────────────────────────────────────────────────────────
# Главный кейс R1: pending activate-задача по target_id удаляемого ада → cancelled.
@pytest.mark.asyncio
async def test_bulk_delete_cancels_orphan_single_task(
    pg_engine, fake_redis_client, clean_bd
) -> None:
    """bulk-delete отменяет pending pause_ad/activate_ad по target_id удалённого ада."""
    suffix = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fb_ad_id = await _seed_ad(conn, suffix)
        pause_id = await _insert_meta_task(conn, "pause_ad", fb_ad_id, "pending", f"{suffix}p")
        act_id = await _insert_meta_task(conn, "activate_ad", fb_ad_id, "retrying", f"{suffix}a")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/dashboard/ads/bulk-delete", json={"fb_ad_ids": [fb_ad_id]})

    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == [fb_ad_id]
    assert data["count"] == 1
    assert set(data["cancelled_task_ids"]) == {pause_id, act_id}

    async with pg_engine.begin() as conn:
        assert await _task_status(conn, pause_id) == "cancelled"
        assert await _task_status(conn, act_id) == "cancelled"


# ─── Тест 2 ──────────────────────────────────────────────────────────────────
# Терминальные задачи (succeeded/failed/cancelled) НЕ трогаем — отменяем только active.
@pytest.mark.asyncio
async def test_bulk_delete_skips_terminal_tasks(pg_engine, fake_redis_client, clean_bd) -> None:
    """Уже succeeded/failed задачи по тому же ad_id не переводятся в cancelled."""
    suffix = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fb_ad_id = await _seed_ad(conn, suffix)
        done_id = await _insert_meta_task(conn, "pause_ad", fb_ad_id, "succeeded", f"{suffix}d")
        fail_id = await _insert_meta_task(conn, "pause_ad", fb_ad_id, "failed", f"{suffix}f")
        run_id = await _insert_meta_task(conn, "pause_ad", fb_ad_id, "running", f"{suffix}r")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/dashboard/ads/bulk-delete", json={"fb_ad_ids": [fb_ad_id]})

    assert resp.status_code == 200
    data = resp.json()
    # Отменяется только running (active); терминальные не трогаются.
    assert data["cancelled_task_ids"] == [run_id]

    async with pg_engine.begin() as conn:
        assert await _task_status(conn, done_id) == "succeeded"
        assert await _task_status(conn, fail_id) == "failed"
        assert await _task_status(conn, run_id) == "cancelled"


# ─── Тест 3 ──────────────────────────────────────────────────────────────────
# bulk_status_change с params.ad_ids, пересекающимся с удаляемыми → cancelled.
@pytest.mark.asyncio
async def test_bulk_delete_cancels_bulk_status_change(
    pg_engine, fake_redis_client, clean_bd
) -> None:
    """bulk_status_change-задача с params.ad_ids ∋ удалённый ad → отменяется."""
    suffix = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        del_ad = await _seed_ad(conn, f"{suffix}d")
        keep_ad = await _seed_ad(conn, f"{suffix}k")
        bulk_id = await _insert_bulk_task(conn, [del_ad, keep_ad], "pending", suffix)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/dashboard/ads/bulk-delete", json={"fb_ad_ids": [del_ad]})

    assert resp.status_code == 200
    data = resp.json()
    assert data["cancelled_task_ids"] == [bulk_id]

    async with pg_engine.begin() as conn:
        assert await _task_status(conn, bulk_id) == "cancelled"


# ─── Тест 4 ──────────────────────────────────────────────────────────────────
# Задачи по НЕ удаляемым ad_id остаются нетронутыми (нет over-cancel).
@pytest.mark.asyncio
async def test_bulk_delete_leaves_unrelated_tasks(
    pg_engine, fake_redis_client, clean_bd
) -> None:
    """Удаление одного ада не отменяет задачи по другому аду."""
    suffix = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        del_ad = await _seed_ad(conn, f"{suffix}d")
        other_ad = await _seed_ad(conn, f"{suffix}o")
        other_task = await _insert_meta_task(conn, "pause_ad", other_ad, "pending", f"{suffix}o")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/dashboard/ads/bulk-delete", json={"fb_ad_ids": [del_ad]})

    assert resp.status_code == 200
    data = resp.json()
    assert data["cancelled_task_ids"] == []

    async with pg_engine.begin() as conn:
        assert await _task_status(conn, other_task) == "pending"
