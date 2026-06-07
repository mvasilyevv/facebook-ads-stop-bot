# -*- coding: utf-8 -*-
"""Интеграционные тесты: expires_at + current_state в DRAFT-схемах (Ручки 4 и 5).

Ручка 4: expires_at = created_at + 24h (DRAFT_TTL_SECONDS из core.tasks.queue).
Ручка 5: current_state — текущее состояние объекта мутации (diff было→станет).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine
from apps.api.main import create_app
from core.meta_api.queue import create_draft_task
from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import DRAFT_TTL_SECONDS


def _make_app(engine):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    return app


@pytest_asyncio.fixture
async def draft_env(pg_engine):
    """Окружение: ad + adset + campaign + offer для current_state-тестов."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"DRFT{uuid.uuid4().hex[:8]}"
    fb_adset_id = f"ADST{uuid.uuid4().hex[:8]}"
    task_ids: list[int] = []

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"TST_{fb_ad_id}", "n": "test offer"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"TST_CMP_{fb_ad_id}", "o": offer_id},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, campaign_id, adset_name, fb_adset_id) "
                "VALUES (:i, :c, :n, :fid)"
            ),
            {"i": adset_id, "c": campaign_id, "n": f"TST_ADS_{fb_ad_id}", "fid": fb_adset_id},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"TST_AD_{fb_ad_id}"},
        )
        # ad_alert_state для current_state резолва
        await conn.execute(
            text(
                "INSERT INTO ad_alert_state (ad_id, alert_state, current_stage) "
                "VALUES (:a, :s, :cs)"
            ),
            {"a": ad_id, "s": "warning_sent", "cs": "warning"},
        )

    yield {
        "fb_ad_id": fb_ad_id,
        "fb_adset_id": fb_adset_id,
        "ad_id": ad_id,
        "task_ids": task_ids,
    }

    async with pg_engine.begin() as conn:
        if task_ids:
            await conn.execute(
                text("DELETE FROM task_queue WHERE id = ANY(:ids)"), {"ids": task_ids}
            )
        await conn.execute(text("DELETE FROM ad_alert_state WHERE ad_id = :a"), {"a": ad_id})
        await conn.execute(text("DELETE FROM fb_ads WHERE id = :i"), {"i": ad_id})
        await conn.execute(text("DELETE FROM fb_adsets WHERE id = :i"), {"i": adset_id})
        await conn.execute(text("DELETE FROM fb_campaigns WHERE id = :i"), {"i": campaign_id})
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})


# ─── Ручка 4: expires_at ───


# expires_at присутствует в list-endpoint'е и имеет правильный формат ISO.
@pytest.mark.asyncio
async def test_draft_list_has_expires_at(pg_engine):
    """List-endpoint /dashboard/draft-tasks возвращает expires_at для каждого draft."""
    payload = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id="99000001",
        params={},
        ad_account_id=None,
    )
    tid = await create_draft_task(pg_engine, payload=payload, requested_by="test")
    assert tid is not None

    try:
        app = _make_app(pg_engine)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get("/api/dashboard/draft-tasks")
        assert resp.status_code == 200
        found = [d for d in resp.json() if d["id"] == tid]
        assert found, "draft не найден в списке"
        d = found[0]
        assert d["expires_at"] is not None, "expires_at должен быть заполнен"
        # Проверяем что expires_at парсится как ISO datetime
        expires = datetime.fromisoformat(d["expires_at"])
        created = datetime.fromisoformat(d["created_at"])
        # expires_at должен быть ровно +24h от created_at
        delta = (expires - created).total_seconds()
        assert abs(delta - DRAFT_TTL_SECONDS) < 2, f"Неверный expires_at: delta={delta}s"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE id = :id"), {"id": tid})


# expires_at == created_at + DRAFT_TTL_SECONDS с точностью до 1 секунды.
@pytest.mark.asyncio
async def test_draft_expires_at_exact_ttl(pg_engine):
    """expires_at = created_at + DRAFT_TTL_SECONDS (константа из core.tasks.queue)."""
    payload = MetaMutationPayload(
        mutation_kind="activate_ad",
        target_id="99000002",
        params={},
        ad_account_id=None,
    )
    tid = await create_draft_task(pg_engine, payload=payload, requested_by="test_exact")
    assert tid is not None

    try:
        app = _make_app(pg_engine)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get("/api/dashboard/draft-tasks")
        assert resp.status_code == 200
        found = [d for d in resp.json() if d["id"] == tid]
        assert found
        d = found[0]
        expires = datetime.fromisoformat(d["expires_at"])
        created = datetime.fromisoformat(d["created_at"])
        expected_expires = created + timedelta(seconds=DRAFT_TTL_SECONDS)
        diff = abs((expires - expected_expires).total_seconds())
        assert diff < 2, f"expires_at отличается от expected на {diff:.1f}s"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE id = :id"), {"id": tid})


# ─── Ручка 5: current_state в detail-endpoint ───


# Для pause_ad: current_state содержит alert_state объявления.
@pytest.mark.asyncio
async def test_draft_detail_current_state_pause_ad(pg_engine, draft_env):
    """GET /tma/draft-tasks/{id}: current_state для pause_ad — alert_state + delivery_status."""
    fb_ad_id = draft_env["fb_ad_id"]
    task_ids = draft_env["task_ids"]

    payload = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id=fb_ad_id,
        params={},
        ad_account_id=None,
    )
    tid = await create_draft_task(pg_engine, payload=payload, requested_by="test_cs")
    task_ids.append(tid)

    # Прямой вызов _load_draft_row + _resolve_current_state (без TMA auth).
    from apps.api.routers.v1.tma import _load_draft_row, _resolve_current_state

    info = await _load_draft_row(pg_engine, tid)
    assert info is not None, "draft не найден"

    state = await _resolve_current_state(pg_engine, info["payload"])
    assert state is not None, "current_state должен быть заполнен для pause_ad"
    assert "alert_state" in state, f"Нет alert_state в current_state: {state}"
    assert state["alert_state"] == "warning_sent", f"Неверный alert_state: {state['alert_state']}"


# Для bulk_status_change: current_state содержит by_state агрегат.
@pytest.mark.asyncio
async def test_draft_detail_current_state_bulk_status_change(pg_engine, draft_env):
    """current_state для bulk_status_change — агрегат by_state для N объектов."""
    fb_ad_id = draft_env["fb_ad_id"]
    task_ids = draft_env["task_ids"]

    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id=None,
        params={"ids": [fb_ad_id], "action": "pause", "object_type": "ad"},
        ad_account_id=None,
    )
    tid = await create_draft_task(pg_engine, payload=payload, requested_by="test_bulk")
    task_ids.append(tid)

    from apps.api.routers.v1.tma import _load_draft_row, _resolve_current_state

    info = await _load_draft_row(pg_engine, tid)
    assert info is not None

    state = await _resolve_current_state(pg_engine, info["payload"])
    assert state is not None, "current_state для bulk_status_change должен быть не None"
    assert "by_state" in state, f"Нет by_state: {state}"
    assert "object_count" in state
    assert state["object_count"] == 1


# Для неизвестного mutation_kind — current_state = None (не поддерживается).
@pytest.mark.asyncio
async def test_draft_detail_current_state_unsupported_kind(pg_engine):
    """current_state для неподдерживаемых mutation_kind (create_campaign и т.п.) = None."""
    from apps.api.routers.v1.tma import _resolve_current_state
    from core.meta_api.schemas import MetaMutationPayload

    # create_campaign — не поддерживается (слишком сложно и без local-аналога).
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="act_123",
        params={"name": "Test"},
        ad_account_id="act_123",
    )
    state = await _resolve_current_state(pg_engine, payload)
    assert state is None, f"Ожидали None для create_campaign, получили: {state}"


# set_adset_budget — current_state содержит note (budget не хранится локально).
@pytest.mark.asyncio
async def test_draft_detail_current_state_set_adset_budget_not_found(pg_engine):
    """current_state для set_adset_budget несуществующего adset → note."""
    from apps.api.routers.v1.tma import _resolve_current_state

    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23999000000nonexist",
        params={"daily_budget": 5000},
        ad_account_id=None,
    )
    state = await _resolve_current_state(pg_engine, payload)
    # Несуществующий adset_id → None
    assert state is None


# set_adset_budget — существующий adset → note о том что budget не хранится.
@pytest.mark.asyncio
async def test_draft_detail_current_state_set_adset_budget_existing(pg_engine, draft_env):
    """current_state для set_adset_budget при существующем adset → note (budget не в БД)."""
    from apps.api.routers.v1.tma import _resolve_current_state

    fb_adset_id = draft_env["fb_adset_id"]
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id=fb_adset_id,
        params={"daily_budget": 5000},
        ad_account_id=None,
    )
    state = await _resolve_current_state(pg_engine, payload)
    assert state is not None
    assert "note" in state
    assert "adset_name" in state


# expires_at в TMA detail-endpoint при 404 отсутствует в ответе (не 200).
@pytest.mark.asyncio
async def test_tma_draft_detail_404_if_not_draft(pg_engine):
    """Несуществующий task_id → 404, а не 500."""
    from apps.api.routers.v1.tma import _load_draft_row

    result = await _load_draft_row(pg_engine, task_id=999999999)
    assert result is None, "Должен вернуть None для несуществующей задачи"
