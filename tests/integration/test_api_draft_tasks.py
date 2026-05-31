# -*- coding: utf-8 -*-
"""Integration: admin-роутер draft-задач (/dashboard/draft-tasks).

Money-критично: confirm переводит DRAFT meta_api_mutation → PENDING (исполнится
meta_api_worker'ом). Admin-зона (X-API-Key, без TG-личности) подтверждает только
безхозные черновики (created_by_chat_id IS NULL, MCP/HTTP); TG-черновики → 409
(подтверждаются в Telegram). ACL-ядро (approve_draft_task) не дублируем — проверяем
поведение через HTTP. Требует Postgres (pg_engine). Cleanup id-scoped по task_queue.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine
from apps.api.main import create_app
from core.meta_api.queue import create_draft_task
from core.meta_api.schemas import MetaMutationPayload


def _make_app(engine):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    return app


@pytest_asyncio.fixture
async def draft_factory(pg_engine):
    """Фабрика DRAFT meta_api_mutation с id-scoped teardown."""
    tasks: list[int] = []

    async def make_draft(
        *,
        created_by_chat_id: int | None,
        mutation_kind: str = "pause_ad",
        target_id: str = "23999000222",
    ) -> int:
        payload = MetaMutationPayload(
            mutation_kind=mutation_kind,
            target_id=target_id,
            params={"reason": "тест"},
            ad_account_id="act_777",
        )
        tid = await create_draft_task(
            pg_engine,
            payload=payload,
            requested_by="ai",
            created_by_chat_id=created_by_chat_id,
        )
        assert tid is not None
        tasks.append(tid)
        return tid

    yield make_draft

    async with pg_engine.begin() as conn:
        if tasks:
            await conn.execute(text("DELETE FROM task_queue WHERE id = ANY(:ids)"), {"ids": tasks})


# Список draft-задач виден на дашборде + правильный shape (mutation_kind, params).
@pytest.mark.asyncio
async def test_list_draft_tasks(pg_engine, draft_factory):
    tid = await draft_factory(created_by_chat_id=None, mutation_kind="set_adset_budget")
    app = _make_app(pg_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get("/api/dashboard/draft-tasks")
    assert resp.status_code == 200, resp.text
    found = [d for d in resp.json() if d["id"] == tid]
    assert found, "созданный draft не виден в списке"
    assert found[0]["mutation_kind"] == "set_adset_budget"
    assert found[0]["payload"].get("reason") == "тест"


# Подтверждение безхозного (MCP/HTTP) черновика с дашборда → 200, статус pending.
@pytest.mark.asyncio
async def test_confirm_ownerless_draft(pg_engine, draft_factory):
    tid = await draft_factory(created_by_chat_id=None)
    app = _make_app(pg_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(f"/api/dashboard/draft-tasks/{tid}/confirm", json={})
    assert resp.status_code == 200, resp.text
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :id"), {"id": tid})
        ).first()
    assert row.status == "pending"


# КРИТИЧНО: подтверждение TG-черновика (created_by_chat_id задан) с дашборда → 409,
# статус остаётся draft (его подтверждают в Telegram, не на десктопе).
@pytest.mark.asyncio
async def test_confirm_tg_draft_forbidden(pg_engine, draft_factory):
    tid = await draft_factory(created_by_chat_id=987654)
    app = _make_app(pg_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(f"/api/dashboard/draft-tasks/{tid}/confirm", json={})
    assert resp.status_code == 409
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :id"), {"id": tid})
        ).first()
    assert row.status == "draft"


# Отклонение черновика с дашборда → 200, статус cancelled.
@pytest.mark.asyncio
async def test_reject_draft(pg_engine, draft_factory):
    tid = await draft_factory(created_by_chat_id=None)
    app = _make_app(pg_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(f"/api/dashboard/draft-tasks/{tid}/reject", json={"reason": "нет"})
    assert resp.status_code == 200, resp.text
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :id"), {"id": tid})
        ).first()
    assert row.status == "cancelled"
