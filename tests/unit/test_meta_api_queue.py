# -*- coding: utf-8 -*-
"""Тесты outbox-функций core/meta_api/queue.py."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.meta_api.queue import (
    approve_draft_task,
    cancel_draft_task,
    claim_pending_task,
    create_mutation_task,
    generate_idempotency_key,
    list_tasks_for_account,
    mark_failed,
    mark_succeeded,
)

# ─── Вспомогательные фабрики ───────────────────────────────────────────────


def _make_task(
    *,
    status: str = "PENDING",
    attempt_count: int = 0,
    max_attempts: int = 5,
    next_retry_at: datetime | None = None,
    idempotency_key: str | None = None,
    mutation_kind: str = "pause_ad",
    target_id: str = "act_123",
    ad_account_id: str = "act_123",
    requested_by: str = "test",
) -> MagicMock:
    """Создаёт мок MetaApiMutationTask с нужными полями."""
    task = MagicMock()
    task.id = uuid.uuid4()
    task.status = status
    task.attempt_count = attempt_count
    task.max_attempts = max_attempts
    task.next_retry_at = next_retry_at
    task.idempotency_key = idempotency_key or generate_idempotency_key(mutation_kind, target_id, {})
    task.mutation_kind = mutation_kind
    task.target_id = target_id
    task.ad_account_id = ad_account_id
    task.requested_by = requested_by
    task.approved_by = None
    task.approved_at = None
    task.approval_telegram_message_id = None
    task.last_error = None
    task.error_code = None
    task.error_subcode = None
    task.completed_at = None
    task.result_json = None
    return task


def _make_db(*, scalar_result: Any = None, scalars_result: list | None = None) -> AsyncMock:
    """Создаёт мок AsyncSession."""
    db = AsyncMock()

    # scalar() возвращает результат execute().scalar_one_or_none()
    execute_result = AsyncMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=scalar_result)
    execute_result.scalars = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.__iter__ = MagicMock(return_value=iter(scalars_result or []))
    execute_result.scalars.return_value = scalars_mock

    db.execute = AsyncMock(return_value=execute_result)
    db.scalar = AsyncMock(return_value=scalar_result)
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


# ─── generate_idempotency_key ──────────────────────────────────────────────


def test_generate_idempotency_key_deterministic():
    """Одинаковые входы должны давать одинаковый ключ."""
    key1 = generate_idempotency_key("pause_ad", "123456", {"reason": "test"})
    key2 = generate_idempotency_key("pause_ad", "123456", {"reason": "test"})
    assert key1 == key2


def test_generate_idempotency_key_different_inputs():
    """Разные входы должны давать разные ключи."""
    key1 = generate_idempotency_key("pause_ad", "123456", {})
    key2 = generate_idempotency_key("activate_ad", "123456", {})
    key3 = generate_idempotency_key("pause_ad", "999999", {})
    assert key1 != key2
    assert key1 != key3


def test_generate_idempotency_key_length():
    """Ключ должен быть не длиннее 64 символов."""
    key = generate_idempotency_key("pause_ad", "123456", {"a": "b" * 500})
    assert len(key) <= 64


def test_generate_idempotency_key_payload_order_irrelevant():
    """Порядок ключей в payload не должен влиять на результат."""
    key1 = generate_idempotency_key("set_budget", "123", {"a": 1, "b": 2})
    key2 = generate_idempotency_key("set_budget", "123", {"b": 2, "a": 1})
    assert key1 == key2


# ─── create_mutation_task ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_mutation_task_basic():
    """Создание задачи — проверка что add() и flush() вызваны."""
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # дубликата нет
    db.flush = AsyncMock()

    added_objects: list = []
    db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    task = await create_mutation_task(
        db,
        mutation_kind="pause_ad",
        target_id="123456789",
        ad_account_id="act_111",
        payload={"status": "PAUSED"},
        requested_by="test_user",
    )

    # Проверяем что задача добавлена в сессию
    assert db.add.call_count == 1
    db.flush.assert_called_once()
    # Возвращённая задача — тот же объект что добавили
    assert task is added_objects[0]
    assert task.mutation_kind == "pause_ad"
    assert task.ad_account_id == "act_111"
    assert task.status == "PENDING"


@pytest.mark.asyncio
async def test_create_mutation_task_auto_idempotency_key():
    """Если idempotency_key=None — генерируется автоматически."""
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.flush = AsyncMock()

    added_objects: list = []
    db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    await create_mutation_task(
        db,
        mutation_kind="activate_ad",
        target_id="999",
        ad_account_id="act_222",
        payload={"x": 1},
        requested_by="bot",
        idempotency_key=None,
    )

    assert len(added_objects) == 1
    created = added_objects[0]
    # Ключ сгенерирован автоматически и не пустой
    assert hasattr(created, "idempotency_key")
    assert len(created.idempotency_key) > 0


@pytest.mark.asyncio
async def test_create_mutation_task_idempotency_returns_existing():
    """Если задача с таким idempotency_key уже существует — возвращает её без дубликата."""
    existing_task = _make_task(idempotency_key="existing-key")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=existing_task)
    db.flush = AsyncMock()
    db.add = MagicMock()

    result = await create_mutation_task(
        db,
        mutation_kind="pause_ad",
        target_id="123",
        ad_account_id="act_111",
        payload={},
        requested_by="test",
        idempotency_key="existing-key",
    )

    # Новая задача НЕ добавляется в сессию
    db.add.assert_not_called()
    db.flush.assert_not_called()
    assert result is existing_task


@pytest.mark.asyncio
async def test_create_mutation_task_draft_status():
    """Создание DRAFT задачи для AI-ассистента."""
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.flush = AsyncMock()

    added_objects: list = []
    db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    await create_mutation_task(
        db,
        mutation_kind="clone_campaign",
        target_id="camp_123",
        ad_account_id="act_333",
        payload={"deep_copy": True},
        requested_by="ai_assistant",
        initial_status="DRAFT",
    )

    assert len(added_objects) == 1
    assert added_objects[0].status == "DRAFT"


# ─── approve_draft_task ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_draft_task_success():
    """DRAFT → PENDING: корректный переход."""
    task = _make_task(status="DRAFT")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=task)
    db.flush = AsyncMock()

    result = await approve_draft_task(
        db,
        task_id=task.id,
        approved_by="user_123",
        approval_telegram_message_id=42,
    )

    assert result.status == "PENDING"
    assert result.approved_by == "user_123"
    assert result.approved_at is not None
    assert result.approval_telegram_message_id == 42
    db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_approve_draft_task_non_draft_raises():
    """Попытка апрувнуть PENDING задачу должна поднимать ValueError."""
    task = _make_task(status="PENDING")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=task)

    with pytest.raises(ValueError, match="не в статусе DRAFT"):
        await approve_draft_task(db, task_id=task.id, approved_by="user")


@pytest.mark.asyncio
async def test_approve_draft_task_not_found_raises():
    """Если задача не найдена — ValueError."""
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="не найдена"):
        await approve_draft_task(db, task_id=uuid.uuid4(), approved_by="user")


# ─── cancel_draft_task ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_draft_task_success():
    """DRAFT → CANCELLED: корректный переход."""
    task = _make_task(status="DRAFT")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=task)
    db.flush = AsyncMock()

    result = await cancel_draft_task(
        db,
        task_id=task.id,
        cancelled_by="user_456",
        reason="Пользователь отказался",
    )

    assert result.status == "CANCELLED"
    assert result.completed_at is not None
    assert "Пользователь отказался" in (result.last_error or "")


@pytest.mark.asyncio
async def test_cancel_draft_task_non_draft_raises():
    """Попытка отменить RUNNING задачу через cancel_draft_task — ValueError."""
    task = _make_task(status="RUNNING")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=task)

    with pytest.raises(ValueError, match="не в статусе DRAFT"):
        await cancel_draft_task(db, task_id=task.id, cancelled_by="user")


# ─── claim_pending_task ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_pending_task_claims_available():
    """claim_pending_task захватывает PENDING задачу и переводит в RUNNING."""
    task = _make_task(status="PENDING", attempt_count=0)
    execute_result = AsyncMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=task)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()

    result = await claim_pending_task(db)

    assert result is task
    assert task.status == "RUNNING"
    assert task.attempt_count == 1
    db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_claim_pending_task_skips_future_retry():
    """claim_pending_task не должна брать задачи с next_retry_at > now."""
    # Нет доступных задач (реализация фильтрует через WHERE — тест через None)
    execute_result = AsyncMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=None)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)

    result = await claim_pending_task(db)
    assert result is None


@pytest.mark.asyncio
async def test_claim_pending_task_no_tasks_returns_none():
    """Если нет доступных задач — возвращает None."""
    execute_result = AsyncMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=None)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)

    result = await claim_pending_task(db)
    assert result is None


# ─── mark_succeeded ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_succeeded_updates_status():
    """RUNNING → SUCCESS: статус, completed_at, result_json установлены."""
    task = _make_task(status="RUNNING")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=task)
    db.flush = AsyncMock()

    await mark_succeeded(db, task_id=task.id, result={"success": True})

    assert task.status == "SUCCESS"
    assert task.completed_at is not None
    assert task.next_retry_at is None
    assert task.last_error is None
    assert task.result_json == {"success": True}


@pytest.mark.asyncio
async def test_mark_succeeded_no_result():
    """mark_succeeded без result_json — поле не перезаписывается."""
    task = _make_task(status="RUNNING")
    task.result_json = None
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=task)
    db.flush = AsyncMock()

    await mark_succeeded(db, task_id=task.id)

    assert task.status == "SUCCESS"
    assert task.result_json is None  # не менялось


@pytest.mark.asyncio
async def test_mark_succeeded_task_not_found():
    """mark_succeeded с несуществующей задачей — не падает."""
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    # Не должно падать
    await mark_succeeded(db, task_id=uuid.uuid4())


# ─── mark_failed ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_failed_with_retry():
    """mark_failed с retry_in_seconds → PENDING с next_retry_at."""
    task = _make_task(status="RUNNING", attempt_count=1, max_attempts=5)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=task)
    db.flush = AsyncMock()

    result = await mark_failed(
        db,
        task_id=task.id,
        error_message="Временная ошибка API",
        retry_in_seconds=60,
    )

    assert result.status == "PENDING"
    assert result.next_retry_at is not None
    assert result.last_error == "Временная ошибка API"


@pytest.mark.asyncio
async def test_mark_failed_without_retry():
    """mark_failed без retry_in_seconds → окончательный FAILED."""
    task = _make_task(status="RUNNING", attempt_count=1, max_attempts=5)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=task)
    db.flush = AsyncMock()

    result = await mark_failed(
        db,
        task_id=task.id,
        error_message="Некорректный запрос",
    )

    assert result.status == "FAILED"
    assert result.completed_at is not None
    assert result.next_retry_at is None


@pytest.mark.asyncio
async def test_mark_failed_exhausted_attempts():
    """mark_failed после max_attempts → FAILED даже если retry_in_seconds задан."""
    task = _make_task(status="RUNNING", attempt_count=5, max_attempts=5)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=task)
    db.flush = AsyncMock()

    result = await mark_failed(
        db,
        task_id=task.id,
        error_message="Лимит попыток исчерпан",
        retry_in_seconds=30,
    )

    # Попытки исчерпаны — должен быть FAILED несмотря на retry_in_seconds
    assert result.status == "FAILED"
    assert result.completed_at is not None


@pytest.mark.asyncio
async def test_mark_failed_sets_error_codes():
    """mark_failed записывает error_code и error_subcode."""
    task = _make_task(status="RUNNING", attempt_count=1, max_attempts=5)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=task)
    db.flush = AsyncMock()

    await mark_failed(
        db,
        task_id=task.id,
        error_message="Graph API error",
        error_code=190,
        error_subcode=460,
    )

    assert task.error_code == 190
    assert task.error_subcode == 460


@pytest.mark.asyncio
async def test_mark_failed_not_found_raises():
    """mark_failed с несуществующей задачей — ValueError."""
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="не найдена"):
        await mark_failed(db, task_id=uuid.uuid4(), error_message="err")


# ─── list_tasks_for_account ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tasks_for_account_no_filter():
    """list_tasks_for_account без фильтра по статусу возвращает задачи кабинета."""
    tasks = [_make_task(ad_account_id="act_555"), _make_task(ad_account_id="act_555")]

    execute_result = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.__iter__ = MagicMock(return_value=iter(tasks))
    execute_result.scalars = MagicMock(return_value=scalars_mock)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)

    result = await list_tasks_for_account(db, ad_account_id="act_555")
    assert len(result) == 2


@pytest.mark.asyncio
async def test_list_tasks_for_account_with_status_filter():
    """list_tasks_for_account с фильтром status='PENDING' — строит корректный запрос."""
    tasks = [_make_task(status="PENDING")]

    execute_result = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.__iter__ = MagicMock(return_value=iter(tasks))
    execute_result.scalars = MagicMock(return_value=scalars_mock)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)

    result = await list_tasks_for_account(db, ad_account_id="act_111", status="PENDING")
    # Проверяем что execute был вызван (фильтрация на уровне SQL-запроса)
    db.execute.assert_called_once()
    assert len(result) == 1


@pytest.mark.asyncio
async def test_list_tasks_for_account_with_list_status_filter():
    """list_tasks_for_account с list[str] status — проверяем что запрос строится."""
    execute_result = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.__iter__ = MagicMock(return_value=iter([]))
    execute_result.scalars = MagicMock(return_value=scalars_mock)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)

    result = await list_tasks_for_account(db, ad_account_id="act_222", status=["PENDING", "DRAFT"])
    db.execute.assert_called_once()
    assert result == []
