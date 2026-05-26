# -*- coding: utf-8 -*-
"""Unit-тесты для draft-tasks endpoints в /api/tma."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.deps import get_db, require_tma_session

FAKE_USER_ID = "42"
FAKE_USERNAME = "test_owner"


def _make_task_mock(
    *,
    task_id: uuid.UUID | None = None,
    kind: str = "set_budget",
    target_id: str = "120201234567890",
    ad_account_id: str = "act_123",
    status: str = "DRAFT",
    payload: dict | None = None,
    requested_by: str = "ai_assistant",
) -> MagicMock:
    """Создаёт мок MetaApiMutationTask для подстановки в db.scalar/db.execute."""
    task = MagicMock()
    task.id = task_id or uuid.uuid4()
    task.mutation_kind = kind
    task.target_id = target_id
    task.ad_account_id = ad_account_id
    task.status = status
    task.payload_json = payload or {"daily_budget_cents": 5000, "reason": "test"}
    task.requested_by = requested_by
    task.approved_by = None
    task.approved_at = None
    task.created_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    task.last_error = None
    task.attempt_count = 0
    return task


def _make_app() -> FastAPI:
    """Создаёт изолированное FastAPI-приложение с TMA-роутером."""
    settings_mock = MagicMock()
    settings_mock.api_key = "test_api_key"
    settings_mock.tma_session_ttl_seconds = 3600
    settings_mock.telegram_bot_token = "fake_token"

    mini = FastAPI()
    with (
        patch("apps.api.routers.tma.get_settings", return_value=settings_mock),
        patch("apps.api.deps.get_settings", return_value=settings_mock),
    ):
        from apps.api.routers import tma as tma_router

        mini.include_router(tma_router.router)
    return mini


async def _fake_session_owner(request: Request) -> None:
    """Owner-сессия: имеет право подтверждать draft-tasks."""
    request.state.tma_user_id = FAKE_USER_ID
    request.state.tma_role = "owner"
    request.state.tma_username = FAKE_USERNAME


async def _fake_session_recipient(request: Request) -> None:
    """Recipient-сессия: не имеет права на draft-tasks."""
    request.state.tma_user_id = FAKE_USER_ID
    request.state.tma_role = "recipient"
    request.state.tma_username = FAKE_USERNAME


class _FakeDB:
    """Минимальный мок AsyncSession, программируемый по execute/scalar/commit."""

    def __init__(self) -> None:
        self.scalar = AsyncMock()
        self.execute = AsyncMock()
        self.commit = AsyncMock()

    async def __aenter__(self):  # noqa: D401 — protocol method
        return self

    async def __aexit__(self, *_):  # noqa: D401
        return False


@pytest.fixture()
def fake_db() -> _FakeDB:
    return _FakeDB()


@pytest.fixture()
def app_owner(fake_db: _FakeDB) -> FastAPI:
    """FastAPI с owner-сессией и подмененной get_db."""
    a = _make_app()
    a.dependency_overrides[require_tma_session] = _fake_session_owner

    async def _override_db():
        yield fake_db

    a.dependency_overrides[get_db] = _override_db
    return a


@pytest.fixture()
def app_recipient(fake_db: _FakeDB) -> FastAPI:
    """FastAPI с recipient-сессией."""
    a = _make_app()
    a.dependency_overrides[require_tma_session] = _fake_session_recipient

    async def _override_db():
        yield fake_db

    a.dependency_overrides[get_db] = _override_db
    return a


# ─── GET /api/tma/draft-tasks ─────────────────────────────────────────────


# Сценарий: owner получает список из 2 draft-задач, summary заполнен.
def test_list_draft_tasks_owner_200(app_owner: FastAPI, fake_db: _FakeDB):
    tasks = [
        _make_task_mock(kind="set_budget", target_id="999", payload={"daily_budget_cents": 5000}),
        _make_task_mock(
            kind="clone_campaign",
            target_id="",
            payload={"source_campaign_id": "111", "deep_copy": True},
        ),
    ]
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=tasks)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    fake_db.execute.return_value = result_mock

    with TestClient(app_owner) as c:
        resp = c.get("/api/tma/draft-tasks")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["mutation_kind"] == "set_budget"
    assert "Бюджет" in data[0]["summary"]
    assert data[1]["mutation_kind"] == "clone_campaign"
    assert "Клон" in data[1]["summary"]


# Сценарий: recipient получает 403, не видит список draft.
def test_list_draft_tasks_recipient_403(app_recipient: FastAPI):
    with TestClient(app_recipient) as c:
        resp = c.get("/api/tma/draft-tasks")
    assert resp.status_code == 403


# Сценарий: owner получает пустой список — endpoint возвращает [].
def test_list_draft_tasks_empty(app_owner: FastAPI, fake_db: _FakeDB):
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[])
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    fake_db.execute.return_value = result_mock

    with TestClient(app_owner) as c:
        resp = c.get("/api/tma/draft-tasks")

    assert resp.status_code == 200
    assert resp.json() == []


# ─── GET /api/tma/draft-tasks/{task_id} ───────────────────────────────────


# Сценарий: owner получает детали draft-task с payload и метаданными.
def test_get_draft_task_detail_200(app_owner: FastAPI, fake_db: _FakeDB):
    task_id = uuid.uuid4()
    task = _make_task_mock(
        task_id=task_id,
        kind="bulk_pause",
        target_id="",
        payload={"ad_ids": ["a1", "a2"], "filter": {"offer_code": "DRC_CR2"}},
    )
    fake_db.scalar.return_value = task

    with TestClient(app_owner) as c:
        resp = c.get(f"/api/tma/draft-tasks/{task_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(task_id)
    assert data["mutation_kind"] == "bulk_pause"
    assert data["payload"]["ad_ids"] == ["a1", "a2"]


# Сценарий: некорректный UUID → 400.
def test_get_draft_task_bad_uuid_400(app_owner: FastAPI):
    with TestClient(app_owner) as c:
        resp = c.get("/api/tma/draft-tasks/not-a-uuid")
    assert resp.status_code == 400


# Сценарий: задача не найдена → 404.
def test_get_draft_task_not_found_404(app_owner: FastAPI, fake_db: _FakeDB):
    fake_db.scalar.return_value = None
    with TestClient(app_owner) as c:
        resp = c.get(f"/api/tma/draft-tasks/{uuid.uuid4()}")
    assert resp.status_code == 404


# ─── POST /api/tma/draft-tasks/{task_id}/confirm ──────────────────────────


# Сценарий: owner успешно подтверждает draft → 200, status в ответе PENDING.
def test_confirm_draft_task_200(app_owner: FastAPI, fake_db: _FakeDB):
    task_id = uuid.uuid4()
    approved_task = _make_task_mock(task_id=task_id, status="PENDING")
    approved_task.approved_by = FAKE_USERNAME

    with patch(
        "apps.api.routers.tma.approve_draft_task",
        new=AsyncMock(return_value=approved_task),
    ):
        with TestClient(app_owner) as c:
            resp = c.post(f"/api/tma/draft-tasks/{task_id}/confirm")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "PENDING"
    assert data["approved_by"] == FAKE_USERNAME


# Сценарий: задача не в DRAFT → approve_draft_task бросает ValueError → 409.
def test_confirm_draft_task_409_wrong_status(app_owner: FastAPI):
    task_id = uuid.uuid4()
    with patch(
        "apps.api.routers.tma.approve_draft_task",
        new=AsyncMock(side_effect=ValueError("не в статусе DRAFT")),
    ):
        with TestClient(app_owner) as c:
            resp = c.post(f"/api/tma/draft-tasks/{task_id}/confirm")
    assert resp.status_code == 409


# Сценарий: recipient пытается подтвердить → 403.
def test_confirm_draft_task_recipient_403(app_recipient: FastAPI):
    task_id = uuid.uuid4()
    with TestClient(app_recipient) as c:
        resp = c.post(f"/api/tma/draft-tasks/{task_id}/confirm")
    assert resp.status_code == 403


# ─── POST /api/tma/draft-tasks/{task_id}/reject ───────────────────────────


# Сценарий: owner отменяет draft → 200, status CANCELLED.
def test_reject_draft_task_200(app_owner: FastAPI, fake_db: _FakeDB):
    task_id = uuid.uuid4()
    cancelled_task = _make_task_mock(task_id=task_id, status="CANCELLED")

    with patch(
        "apps.api.routers.tma.cancel_draft_task",
        new=AsyncMock(return_value=cancelled_task),
    ):
        with TestClient(app_owner) as c:
            resp = c.post(
                f"/api/tma/draft-tasks/{task_id}/reject",
                json={"reason": "тест-отмена"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "CANCELLED"


# Сценарий: reject без тела (пустой JSON) → 200, reason=None допустим.
def test_reject_draft_task_no_body_200(app_owner: FastAPI, fake_db: _FakeDB):
    task_id = uuid.uuid4()
    cancelled_task = _make_task_mock(task_id=task_id, status="CANCELLED")

    with patch(
        "apps.api.routers.tma.cancel_draft_task",
        new=AsyncMock(return_value=cancelled_task),
    ):
        with TestClient(app_owner) as c:
            resp = c.post(f"/api/tma/draft-tasks/{task_id}/reject", json={})
    assert resp.status_code == 200


# Сценарий: задача не в DRAFT → cancel_draft_task бросает ValueError → 409.
def test_reject_draft_task_409(app_owner: FastAPI):
    task_id = uuid.uuid4()
    with patch(
        "apps.api.routers.tma.cancel_draft_task",
        new=AsyncMock(side_effect=ValueError("не в статусе DRAFT")),
    ):
        with TestClient(app_owner) as c:
            resp = c.post(f"/api/tma/draft-tasks/{task_id}/reject", json={})
    assert resp.status_code == 409


# Сценарий: recipient пытается отменить → 403.
def test_reject_draft_task_recipient_403(app_recipient: FastAPI):
    task_id = uuid.uuid4()
    with TestClient(app_recipient) as c:
        resp = c.post(f"/api/tma/draft-tasks/{task_id}/reject", json={})
    assert resp.status_code == 403
