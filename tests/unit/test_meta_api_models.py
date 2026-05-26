# -*- coding: utf-8 -*-
"""Unit-тесты для моделей MetaApiAuditLog и MetaApiMutationTask.

Тесты используют реальный PostgreSQL (уже запущен в docker для проекта),
потому что partial-индексы и CHECK constraints специфичны для PostgreSQL
и требуют именно этого диалекта.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Регистрируем модели в Base.metadata
from core.models import MetaApiAuditLog, MetaApiMutationTask  # noqa: F401

# ---------------------------------------------------------------------------
# Фикстура: PostgreSQL-сессия с автоматическим откатом после теста
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pg_session():
    """PostgreSQL AsyncSession с rollback-at-teardown для изоляции тестов."""
    from core.config import get_settings

    settings = get_settings()
    pg_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(pg_url, future=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        async with session.begin():
            yield session
            await session.rollback()

    await engine.dispose()


# ---------------------------------------------------------------------------
# Тесты MetaApiAuditLog
# ---------------------------------------------------------------------------


# Проверяем создание записи аудит-лога со всеми полями и чтение обратно из БД.
@pytest.mark.asyncio
async def test_audit_log_create_and_read(pg_session: AsyncSession):
    """Все поля MetaApiAuditLog корректно сохраняются и читаются из PostgreSQL."""
    now = datetime.now(UTC)
    entry = MetaApiAuditLog(
        method="POST",
        endpoint="/act_123/insights",
        params_json={"level": "ad", "date_preset": "today"},
        request_body_json={"fields": "spend,impressions"},
        response_status=200,
        response_json={"data": [{"ad_id": "111", "spend": "5.50"}]},
        duration_ms=312,
        initiated_by="meta_api_worker",
        error_code=None,
        error_subcode=None,
        session_id="sess-abc123",
        ad_account_id="act_9876543210",
        created_at=now,
    )
    pg_session.add(entry)
    await pg_session.flush()

    loaded = await pg_session.get(MetaApiAuditLog, entry.id)
    assert loaded is not None
    assert loaded.method == "POST"
    assert loaded.endpoint == "/act_123/insights"
    assert loaded.params_json == {"level": "ad", "date_preset": "today"}
    assert loaded.response_status == 200
    assert loaded.duration_ms == 312
    assert loaded.initiated_by == "meta_api_worker"
    assert loaded.session_id == "sess-abc123"
    assert loaded.ad_account_id == "act_9876543210"


# Проверяем что nullable-поля аудит-лога принимают None без ошибок.
@pytest.mark.asyncio
async def test_audit_log_nullable_fields(pg_session: AsyncSession):
    """Nullable-поля MetaApiAuditLog принимают None без ошибок БД."""
    entry = MetaApiAuditLog(
        method="GET",
        endpoint="/me",
        response_status=200,
        duration_ms=45,
        initiated_by="bot_observer",
    )
    pg_session.add(entry)
    await pg_session.flush()

    loaded = await pg_session.get(MetaApiAuditLog, entry.id)
    assert loaded is not None
    assert loaded.params_json is None
    assert loaded.request_body_json is None
    assert loaded.error_code is None
    assert loaded.session_id is None
    assert loaded.ad_account_id is None


# Проверяем что BigInteger id автоинкрементируется и каждая запись получает уникальный id.
@pytest.mark.asyncio
async def test_audit_log_autoincrement_id(pg_session: AsyncSession):
    """BigInteger id автоинкрементируется — две записи получают разные id."""
    e1 = MetaApiAuditLog(
        method="GET", endpoint="/a", response_status=0, duration_ms=0, initiated_by="x"
    )
    e2 = MetaApiAuditLog(
        method="GET", endpoint="/b", response_status=0, duration_ms=0, initiated_by="x"
    )
    pg_session.add_all([e1, e2])
    await pg_session.flush()

    assert e1.id is not None
    assert e2.id is not None
    assert e1.id != e2.id


# ---------------------------------------------------------------------------
# Тесты MetaApiMutationTask
# ---------------------------------------------------------------------------


# Проверяем создание задачи-мутации и корректность дефолтных значений полей.
@pytest.mark.asyncio
async def test_mutation_task_create_and_defaults(pg_session: AsyncSession):
    """MetaApiMutationTask создаётся с дефолтами: status=PENDING, attempt_count=0, max_attempts=5."""
    task_id = uuid.uuid4()
    task = MetaApiMutationTask(
        id=task_id,
        mutation_kind="pause_ad",
        target_id="120207839012345678",
        ad_account_id="act_9876543210",
        payload_json={"fields": ["status"]},
        idempotency_key=f"pause_ad:{task_id}",
        requested_by="ai_assistant",
    )
    pg_session.add(task)
    await pg_session.flush()

    loaded = await pg_session.get(MetaApiMutationTask, task_id)
    assert loaded is not None
    assert loaded.mutation_kind == "pause_ad"
    assert loaded.target_id == "120207839012345678"
    assert loaded.ad_account_id == "act_9876543210"
    assert loaded.status == "PENDING"
    assert loaded.attempt_count == 0
    assert loaded.max_attempts == 5
    assert loaded.next_retry_at is None
    assert loaded.last_error is None
    assert loaded.approved_by is None
    assert loaded.completed_at is None
    assert loaded.result_json is None


# Проверяем сохранение approval-полей при переходе DRAFT → PENDING.
@pytest.mark.asyncio
async def test_mutation_task_approval_fields(pg_session: AsyncSession):
    """approved_by, approved_at, approval_telegram_message_id сохраняются корректно."""
    task_id = uuid.uuid4()
    now = datetime.now(UTC)
    task = MetaApiMutationTask(
        id=task_id,
        mutation_kind="set_budget",
        target_id="adset_999",
        ad_account_id="act_111",
        payload_json={"daily_budget": 5000},
        status="DRAFT",
        idempotency_key=f"set_budget:{task_id}",
        requested_by="ai_assistant",
        approved_by="manual_tg:12345678",
        approved_at=now,
        approval_telegram_message_id=9876543,
    )
    pg_session.add(task)
    await pg_session.flush()

    loaded = await pg_session.get(MetaApiMutationTask, task_id)
    assert loaded is not None
    assert loaded.status == "DRAFT"
    assert loaded.approved_by == "manual_tg:12345678"
    assert loaded.approval_telegram_message_id == 9876543


# Проверяем что вставка двух задач с одинаковым idempotency_key вызывает IntegrityError.
@pytest.mark.asyncio
async def test_mutation_task_idempotency_key_unique(pg_session: AsyncSession):
    """Дублирующийся idempotency_key → IntegrityError (UNIQUE constraint)."""
    key = f"pause_ad:unique-test-{uuid.uuid4()}"
    t1 = MetaApiMutationTask(
        id=uuid.uuid4(),
        mutation_kind="pause_ad",
        target_id="ad_001",
        ad_account_id="act_111",
        payload_json={},
        idempotency_key=key,
        requested_by="bot_auto",
    )
    t2 = MetaApiMutationTask(
        id=uuid.uuid4(),
        mutation_kind="pause_ad",
        target_id="ad_002",
        ad_account_id="act_111",
        payload_json={},
        idempotency_key=key,  # тот же ключ!
        requested_by="bot_auto",
    )
    pg_session.add_all([t1, t2])

    with pytest.raises(IntegrityError):
        await pg_session.flush()


# Проверяем что CHECK constraint на status отклоняет недопустимое значение 'INVALID'.
@pytest.mark.asyncio
async def test_mutation_task_invalid_status_check_constraint(pg_session: AsyncSession):
    """status='INVALID' нарушает CHECK constraint → IntegrityError в PostgreSQL."""
    task = MetaApiMutationTask(
        id=uuid.uuid4(),
        mutation_kind="activate_ad",
        target_id="ad_bad",
        ad_account_id="act_222",
        payload_json={},
        idempotency_key=f"bad-status-{uuid.uuid4()}",
        requested_by="manual_tg:111",
        status="INVALID",  # недопустимый статус
    )
    pg_session.add(task)

    with pytest.raises(IntegrityError):
        await pg_session.flush()


# Проверяем что SELECT-запрос по status (indexed column) корректно фильтрует задачи.
@pytest.mark.asyncio
async def test_mutation_task_query_by_status(pg_session: AsyncSession):
    """SELECT по status корректно возвращает задачи с нужным статусом."""
    t1 = MetaApiMutationTask(
        id=uuid.uuid4(),
        mutation_kind="pause_ad",
        target_id="ad_q1",
        ad_account_id="act_333",
        payload_json={},
        status="PENDING",
        idempotency_key=f"q-pending-{uuid.uuid4()}",
        requested_by="bot_auto",
    )
    t2 = MetaApiMutationTask(
        id=uuid.uuid4(),
        mutation_kind="activate_ad",
        target_id="ad_q2",
        ad_account_id="act_333",
        payload_json={},
        status="SUCCESS",
        idempotency_key=f"q-success-{uuid.uuid4()}",
        requested_by="bot_auto",
    )
    pg_session.add_all([t1, t2])
    await pg_session.flush()

    result = await pg_session.execute(
        select(MetaApiMutationTask).where(MetaApiMutationTask.status == "PENDING")
    )
    pending = result.scalars().all()
    assert any(t.id == t1.id for t in pending)
    assert all(t.status == "PENDING" for t in pending)
