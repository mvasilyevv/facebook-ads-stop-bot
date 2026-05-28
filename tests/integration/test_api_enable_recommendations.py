# -*- coding: utf-8 -*-
"""Интеграционные тесты GET/POST /api/dashboard/enable-recommendations.

Проверяет фильтрацию по PENDING/PROMOTED, JOIN с task_queue для promoted_task_status,
создание enable-задачи через /enable и защиту от двойного подтверждения.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

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
async def clean_reco(pg_engine):
    """Очищает enable_recommendations и связанные данные."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM enable_recommendations WHERE idempotency_key LIKE 'reco_test_%'")
            )
            # Удаляем тестовые enable-задачи созданные в тестах
            await conn.execute(
                text("DELETE FROM task_queue WHERE idempotency_key LIKE 'enable:99RECO%'")
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE '99RECO%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'RECO_%'"))
            await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'RECO_%'"))
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'RECO_%'"))

    await _cleanup()
    yield
    await _cleanup()


async def _seed_chain(conn, suffix: str) -> tuple[str, uuid.UUID, uuid.UUID]:
    """Создаёт offer→campaign→adset→ad. Возвращает (fb_ad_id, adset_id, ad_id)."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"99RECO{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"RECO_{suffix}", "n": f"Reco offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"RECO_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"RECO_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"Reco AD {suffix}"},
    )
    return fb_ad_id, adset_id, ad_id


async def _insert_reco(
    conn,
    ad_id: uuid.UUID,
    suffix: str,
    promoted_to_task_id: int | None = None,
) -> uuid.UUID:
    """Вставляет enable_recommendations. Возвращает id рекомендации."""
    rec_id = uuid.uuid4()
    ikey = f"reco_test_{suffix}"
    metrics = json.dumps({"spend": "100.00", "cost_per_lead": "5.00"})

    await conn.execute(
        text(
            """
            INSERT INTO enable_recommendations
                (id, ad_id, snapshot_metrics, recommendation_level,
                 live_batch_started_at, idempotency_key, promoted_to_task_id)
            VALUES
                (:id, :ad, CAST(:m AS JSONB), 'ok', :lbs, :ik, :ptid)
            """
        ),
        {
            "id": rec_id,
            "ad": ad_id,
            "m": metrics,
            "lbs": datetime.now(UTC),
            "ik": ikey,
            "ptid": promoted_to_task_id,
        },
    )
    return rec_id


# ─── Тест 1 ──────────────────────────────────────────────────────────────────
# Проверяем что пустая таблица возвращает []
@pytest.mark.asyncio
async def test_list_enable_recommendations_empty(pg_engine, fake_redis_client, clean_reco) -> None:
    """GET без записей → пустой список []."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/enable-recommendations")

    assert resp.status_code == 200
    assert resp.json() == []


# ─── Тест 2 ──────────────────────────────────────────────────────────────────
# Проверяем что PENDING фильтрует только неподтверждённые рекомендации
@pytest.mark.asyncio
async def test_list_enable_recommendations_pending_only(
    pg_engine, fake_redis_client, clean_reco
) -> None:
    """?status=PENDING → только рекомендации без promoted_to_task_id."""
    suffix_p = uuid.uuid4().hex[:5]
    suffix_pr = uuid.uuid4().hex[:5]

    async with pg_engine.begin() as conn:
        _, _, ad_id_p = await _seed_chain(conn, suffix_p)
        _, _, ad_id_pr = await _seed_chain(conn, suffix_pr)

        # Создаём promoted task для второй рекомендации
        task_result = await conn.execute(
            text(
                """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload,
                     attempt_count, max_attempts, requested_by)
                VALUES
                    ('enable', 'pending', :ik, '{}', 0, 5, 'test')
                RETURNING id
                """
            ),
            {"ik": f"enable:99RECO{suffix_pr}:promo:{uuid.uuid4().hex}"},
        )
        task_id = task_result.first()[0]

        await _insert_reco(conn, ad_id_p, f"p_{suffix_p}")
        await _insert_reco(conn, ad_id_pr, f"pr_{suffix_pr}", promoted_to_task_id=task_id)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/enable-recommendations?status=PENDING")

    assert resp.status_code == 200
    items = resp.json()
    assert all(item["promoted_to_task_id"] is None for item in items)


# ─── Тест 3 ──────────────────────────────────────────────────────────────────
# Проверяем что PROMOTED возвращает только уже подтверждённые рекомендации
@pytest.mark.asyncio
async def test_list_enable_recommendations_promoted_only(
    pg_engine, fake_redis_client, clean_reco
) -> None:
    """?status=PROMOTED → только рекомендации с promoted_to_task_id IS NOT NULL."""
    suffix_p = uuid.uuid4().hex[:5]
    suffix_pr = uuid.uuid4().hex[:5]

    async with pg_engine.begin() as conn:
        _, _, ad_id_p = await _seed_chain(conn, suffix_p)
        _, _, ad_id_pr = await _seed_chain(conn, suffix_pr)

        task_result = await conn.execute(
            text(
                """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload,
                     attempt_count, max_attempts, requested_by)
                VALUES
                    ('enable', 'running', :ik, '{}', 0, 5, 'test')
                RETURNING id
                """
            ),
            {"ik": f"enable:99RECO{suffix_pr}:promo2:{uuid.uuid4().hex}"},
        )
        task_id = task_result.first()[0]

        await _insert_reco(conn, ad_id_p, f"pp_{suffix_p}")
        await _insert_reco(conn, ad_id_pr, f"ppr_{suffix_pr}", promoted_to_task_id=task_id)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/enable-recommendations?status=PROMOTED")

    assert resp.status_code == 200
    items = resp.json()
    assert all(item["promoted_to_task_id"] is not None for item in items)


# ─── Тест 4 ──────────────────────────────────────────────────────────────────
# Проверяем что promoted_task_status корректно возвращается через JOIN
@pytest.mark.asyncio
async def test_list_enable_recommendations_promoted_task_status(
    pg_engine, fake_redis_client, clean_reco
) -> None:
    """JOIN с task_queue → promoted_task_status возвращается в UPPERCASE."""
    suffix = uuid.uuid4().hex[:5]

    async with pg_engine.begin() as conn:
        _, _, ad_id = await _seed_chain(conn, suffix)

        task_result = await conn.execute(
            text(
                """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload,
                     attempt_count, max_attempts, requested_by)
                VALUES
                    ('enable', 'succeeded', :ik, '{}', 0, 5, 'test')
                RETURNING id
                """
            ),
            {"ik": f"enable:99RECO{suffix}:status:{uuid.uuid4().hex}"},
        )
        task_id = task_result.first()[0]
        await _insert_reco(conn, ad_id, f"jst_{suffix}", promoted_to_task_id=task_id)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/enable-recommendations?status=PROMOTED")

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    assert items[0]["promoted_task_status"] == "SUCCEEDED"


# ─── Тест 5 ──────────────────────────────────────────────────────────────────
# Проверяем создание enable-задачи через POST /enable
@pytest.mark.asyncio
async def test_confirm_enable_recommendation_happy(
    pg_engine, fake_redis_client, clean_reco
) -> None:
    """POST /enable happy path → 201, task создан, promoted_to_task_id выставлен."""
    suffix = uuid.uuid4().hex[:5]

    async with pg_engine.begin() as conn:
        fb_ad_id, _, ad_id = await _seed_chain(conn, suffix)
        rec_id = await _insert_reco(conn, ad_id, f"hap_{suffix}")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/dashboard/enable-recommendations/{rec_id}/enable",
            json={"requested_by": "tester"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["task_type"] == "enable"
    assert data["status"] == "PENDING"
    task_id = int(data["id"])

    # Проверяем что promoted_to_task_id выставлен в БД
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT promoted_to_task_id FROM enable_recommendations WHERE id = :rid"),
                {"rid": rec_id},
            )
        ).first()
    assert row.promoted_to_task_id == task_id


# ─── Тест 6 ──────────────────────────────────────────────────────────────────
# Проверяем защиту от двойного подтверждения рекомендации
@pytest.mark.asyncio
async def test_confirm_enable_recommendation_already_promoted(
    pg_engine, fake_redis_client, clean_reco
) -> None:
    """POST /enable для уже promoted рекомендации → 409."""
    suffix = uuid.uuid4().hex[:5]

    async with pg_engine.begin() as conn:
        _, _, ad_id = await _seed_chain(conn, suffix)

        # Создаём задачу-заглушку для promoted_to_task_id
        task_result = await conn.execute(
            text(
                """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload,
                     attempt_count, max_attempts, requested_by)
                VALUES
                    ('enable', 'pending', :ik, '{}', 0, 5, 'test')
                RETURNING id
                """
            ),
            {"ik": f"enable:99RECO{suffix}:dup:{uuid.uuid4().hex}"},
        )
        task_id = task_result.first()[0]
        rec_id = await _insert_reco(conn, ad_id, f"dup_{suffix}", promoted_to_task_id=task_id)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/api/dashboard/enable-recommendations/{rec_id}/enable")

    assert resp.status_code == 409


# ─── Тест 7 ──────────────────────────────────────────────────────────────────
# Проверяем 404 для несуществующей рекомендации
@pytest.mark.asyncio
async def test_confirm_enable_recommendation_not_found(
    pg_engine, fake_redis_client, clean_reco
) -> None:
    """POST /enable для несуществующей рекомендации → 404."""
    nonexistent_id = uuid.uuid4()

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/api/dashboard/enable-recommendations/{nonexistent_id}/enable")

    assert resp.status_code == 404
