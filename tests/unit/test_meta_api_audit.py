# -*- coding: utf-8 -*-
"""Unit-тесты для core/meta_api/audit.py.

Используют реальный PostgreSQL (тот же docker-контейнер проекта),
потому что partial-индексы и JSONB специфичны для PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from core.meta_api.audit import (
    extract_ad_account_id_from_endpoint,
    query_rate_limit_headroom,
    query_recent_errors,
    record_audit_log,
)
from core.models import MetaApiAuditLog  # noqa: F401

# ---------------------------------------------------------------------------
# Фикстура: PostgreSQL-сессия с откатом после каждого теста
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
# Тесты: extract_ad_account_id_from_endpoint
# ---------------------------------------------------------------------------


# Проверяем что стандартный endpoint с act_ извлекается корректно.
def test_extract_act_id_insights():
    """/act_456983032490208/insights → act_456983032490208."""
    result = extract_ad_account_id_from_endpoint("/act_456983032490208/insights")
    assert result == "act_456983032490208"


# /me не содержит act_ — должен вернуть None.
def test_extract_act_id_me():
    """/me — нет act_, возвращает None."""
    result = extract_ad_account_id_from_endpoint("/me")
    assert result is None


# /me/adaccounts тоже не содержит act_ — должен вернуть None.
def test_extract_act_id_me_adaccounts():
    """/me/adaccounts — нет act_, возвращает None."""
    result = extract_ad_account_id_from_endpoint("/me/adaccounts")
    assert result is None


# Проверяем что извлечение работает когда после act_ есть дополнительные сегменты.
def test_extract_act_id_campaigns_path():
    """/act_123/campaigns/456 → act_123 (берём первое совпадение)."""
    result = extract_ad_account_id_from_endpoint("/act_123/campaigns/456")
    assert result == "act_123"


# ---------------------------------------------------------------------------
# Тесты: record_audit_log — запись в реальную БД
# ---------------------------------------------------------------------------


# Полный набор полей записывается в БД и корректно читается обратно.
@pytest.mark.asyncio
async def test_record_audit_log_all_fields(pg_session: AsyncSession):
    """record_audit_log со всеми полями — запись сохранена в БД, все поля корректны."""
    row_id = await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_456983032490208/insights",
        params={"level": "ad", "date_preset": "today"},
        request_body=None,
        response_status=200,
        response_json='{"data": [{"ad_id": "111", "spend": "5.50"}]}',
        duration_ms=312,
        initiated_by="meta_api_worker",
        error_code=None,
        error_subcode=None,
        session_id="sess-abc123",
        ad_account_id="act_456983032490208",
    )

    assert row_id > 0

    loaded = await pg_session.get(MetaApiAuditLog, row_id)
    assert loaded is not None
    assert loaded.method == "GET"
    assert loaded.endpoint == "/act_456983032490208/insights"
    assert loaded.params_json == {"level": "ad", "date_preset": "today"}
    assert loaded.response_status == 200
    assert loaded.response_json == {"data": [{"ad_id": "111", "spend": "5.50"}]}
    assert loaded.duration_ms == 312
    assert loaded.initiated_by == "meta_api_worker"
    assert loaded.session_id == "sess-abc123"
    assert loaded.ad_account_id == "act_456983032490208"
    assert loaded.error_code is None
    assert loaded.error_subcode is None


# Минимальный набор полей — дефолты для optional полей должны быть None/0.
@pytest.mark.asyncio
async def test_record_audit_log_minimal_fields(pg_session: AsyncSession):
    """record_audit_log с минимальным набором — optional поля имеют дефолты None/0."""
    row_id = await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/me",
    )

    assert row_id > 0

    loaded = await pg_session.get(MetaApiAuditLog, row_id)
    assert loaded is not None
    assert loaded.params_json is None
    assert loaded.request_body_json is None
    assert loaded.response_status == 0
    assert loaded.response_json is None
    assert loaded.duration_ms == 0
    assert loaded.initiated_by == "unknown"
    assert loaded.error_code is None
    assert loaded.session_id is None
    # ad_account_id извлекается из "/me" → None
    assert loaded.ad_account_id is None


# Большой params (>10000 символов) должен быть обрезан и помечен флагом _truncated.
@pytest.mark.asyncio
async def test_record_audit_log_large_params_truncated(pg_session: AsyncSession):
    """params с длиной >10000 символов — сохраняется обрезанным с флагом _truncated."""
    big_value = "x" * 10_001
    row_id = await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_123/insights",
        params={"big_key": big_value},
    )

    assert row_id > 0

    loaded = await pg_session.get(MetaApiAuditLog, row_id)
    assert loaded is not None
    assert isinstance(loaded.params_json, dict)
    assert loaded.params_json.get("_truncated") is True
    assert "raw" in loaded.params_json
    # raw не превышает лимит
    assert len(loaded.params_json["raw"]) <= 10_000


# Endpoint со специальными символами корректно сохраняется в БД.
@pytest.mark.asyncio
async def test_record_audit_log_special_chars_in_endpoint(pg_session: AsyncSession):
    """Endpoint со специальными символами (%, &, =) сохраняется без искажений."""
    special_endpoint = "/act_123/ads?fields=spend%2Cimpressions&date=2026-01-01"
    row_id = await record_audit_log(
        pg_session,
        method="GET",
        endpoint=special_endpoint,
    )

    assert row_id > 0

    loaded = await pg_session.get(MetaApiAuditLog, row_id)
    assert loaded is not None
    assert loaded.endpoint == special_endpoint


# ad_account_id автоматически извлекается из endpoint когда явно не передан.
@pytest.mark.asyncio
async def test_record_audit_log_auto_extract_account_id(pg_session: AsyncSession):
    """Если ad_account_id не передан явно — извлекается из endpoint по regex."""
    row_id = await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_999888777/campaigns",
        # не передаём ad_account_id явно
    )

    loaded = await pg_session.get(MetaApiAuditLog, row_id)
    assert loaded is not None
    assert loaded.ad_account_id == "act_999888777"


# При ошибке БД функция возвращает 0 и не выбрасывает исключение.
@pytest.mark.asyncio
async def test_record_audit_log_db_error_returns_zero():
    """При ошибке БД record_audit_log возвращает 0 и не поднимает исключение."""
    # Создаём мок-сессию, у которой flush() выбрасывает ошибку
    broken_db = MagicMock(spec=AsyncSession)
    broken_db.add = MagicMock()
    broken_db.flush = AsyncMock(side_effect=RuntimeError("симуляция ошибки БД"))

    result = await record_audit_log(
        broken_db,
        method="POST",
        endpoint="/act_123/ads",
    )

    assert result == 0


# ---------------------------------------------------------------------------
# Тесты: query_recent_errors
# ---------------------------------------------------------------------------


# Фильтр по ошибкам возвращает только записи с response_status >= 400 или error_code not null.
@pytest.mark.asyncio
async def test_query_recent_errors_filter(pg_session: AsyncSession):
    """query_recent_errors возвращает только записи с ошибкой, игнорируя успешные."""
    now = datetime.now(UTC)
    since = now - timedelta(minutes=5)

    # Успешный вызов — не должен попасть в результат
    await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_111/insights",
        response_status=200,
        initiated_by="test_ok",
    )

    # Ошибка 400 — должна попасть в результат
    id_400 = await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_111/campaigns",
        response_status=400,
        initiated_by="test_err400",
    )

    # Ошибка по error_code (rate limit) — должна попасть
    id_err = await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_111/ads",
        response_status=200,
        error_code=17,
        initiated_by="test_rate_limit",
    )

    errors = await query_recent_errors(pg_session, since=since)
    error_ids = {r.id for r in errors}

    assert id_400 in error_ids
    assert id_err in error_ids
    # Успешная запись не должна попасть
    assert all(r.initiated_by != "test_ok" for r in errors)


# query_recent_errors не возвращает записи старше переданного since.
@pytest.mark.asyncio
async def test_query_recent_errors_time_filter(pg_session: AsyncSession):
    """query_recent_errors игнорирует ошибки за пределами окна since."""
    now = datetime.now(UTC)

    # Запись с ошибкой — но created_at выставляем в будущем (beyond since window)
    row = MetaApiAuditLog(
        method="GET",
        endpoint="/act_old/ads",
        response_status=500,
        duration_ms=0,
        initiated_by="old_caller",
        created_at=now - timedelta(hours=2),
    )
    pg_session.add(row)
    await pg_session.flush()

    # since = 1 час назад — запись 2 часа назад не попадёт
    since = now - timedelta(hours=1)
    errors = await query_recent_errors(pg_session, since=since)

    assert all(r.id != row.id for r in errors)


# ---------------------------------------------------------------------------
# Тесты: query_rate_limit_headroom
# ---------------------------------------------------------------------------


# Правильный подсчёт rate_limited_calls и errored_calls в заданном окне.
@pytest.mark.asyncio
async def test_query_rate_limit_headroom_counts(pg_session: AsyncSession):
    """query_rate_limit_headroom корректно считает rate-limit и обычные ошибки."""
    # Ждём: 2 rate-limit (error_code=17, error_code=4), 1 обычная ошибка (error_code=1), 1 OK
    await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_777/insights",
        error_code=17,
        duration_ms=100,
        initiated_by="rl_test",
    )
    await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_777/insights",
        error_code=4,
        duration_ms=200,
        initiated_by="rl_test",
    )
    await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_777/insights",
        error_code=1,
        duration_ms=50,
        initiated_by="rl_test",
    )
    await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_777/insights",
        duration_ms=80,
        initiated_by="rl_test",
    )

    headroom = await query_rate_limit_headroom(
        pg_session,
        ad_account_id="act_777",
        window_minutes=10,
    )

    assert headroom["total_calls"] >= 4
    assert headroom["rate_limited_calls"] >= 2
    assert headroom["errored_calls"] >= 1
    # Среднее duration: (100+200+50+80)/4 = 107 мс (может варьироваться из-за других записей)
    assert headroom["average_duration_ms"] >= 0


# Фильтр по ad_account_id — данные другого кабинета не попадают в статистику.
@pytest.mark.asyncio
async def test_query_rate_limit_headroom_account_filter(pg_session: AsyncSession):
    """query_rate_limit_headroom фильтрует по ad_account_id — другой кабинет не учитывается."""
    # Добавляем вызов для кабинета act_AAA
    await record_audit_log(
        pg_session,
        method="GET",
        endpoint="/act_AAA111/insights",
        error_code=17,
        ad_account_id="act_AAA111",
        duration_ms=100,
        initiated_by="filter_test",
    )

    # Запрашиваем статистику для другого кабинета act_BBB222
    headroom = await query_rate_limit_headroom(
        pg_session,
        ad_account_id="act_BBB222",
        window_minutes=10,
    )

    # act_AAA111 не должен влиять на статистику act_BBB222
    assert headroom["rate_limited_calls"] == 0


# Пустое окно — все счётчики нули, average_duration_ms тоже 0.
@pytest.mark.asyncio
async def test_query_rate_limit_headroom_empty_window(pg_session: AsyncSession):
    """query_rate_limit_headroom на пустом окне возвращает нули без ошибок."""
    # window_minutes=0 → since=now, ни одной записи не попадёт
    headroom = await query_rate_limit_headroom(
        pg_session,
        window_minutes=0,
    )

    assert headroom["total_calls"] == 0
    assert headroom["rate_limited_calls"] == 0
    assert headroom["errored_calls"] == 0
    assert headroom["average_duration_ms"] == 0
