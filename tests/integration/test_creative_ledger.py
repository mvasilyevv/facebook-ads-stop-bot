# -*- coding: utf-8 -*-
"""Integration-тесты: аллокатор кодов креативов + реестр.

CI-only: требует изолированной тестовой БД (фикстура pg_engine из conftest).
НЕ гонять на боевой :5433.

Тестовые строки с префиксом TST_* чистятся в teardown.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.campaign_builder.creative_ledger import (
    allocate_code_span,
    peek_next_seq,
    record_creative,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def clean_ledger(pg_engine):
    """Чистим тестовые строки с префиксом TST_ до и после каждого теста."""
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM campaign_creative WHERE offer_code LIKE 'TST_%'"))
        await conn.execute(text("DELETE FROM offer_creative_seq WHERE offer_code LIKE 'TST_%'"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM campaign_creative WHERE offer_code LIKE 'TST_%'"))
        await conn.execute(text("DELETE FROM offer_creative_seq WHERE offer_code LIKE 'TST_%'"))


# Два последовательных резерва дают непересекающиеся диапазоны (1..4, затем 5..7).
async def test_allocate_sequential(pg_engine):
    async with pg_engine.begin() as conn:
        base1 = await allocate_code_span(conn, "TST_LEDGER", 4)
        base2 = await allocate_code_span(conn, "TST_LEDGER", 3)
    assert base1 == 1 and base2 == 5


# peek_next_seq не изменяет счётчик (два вызова подряд возвращают одно значение).
async def test_peek_does_not_advance(pg_engine):
    async with pg_engine.begin() as conn:
        await allocate_code_span(conn, "TST_PEEK", 2)
        p1 = await peek_next_seq(conn, "TST_PEEK")
        p2 = await peek_next_seq(conn, "TST_PEEK")
    assert p1 == p2 == 2


# Повторная запись того же (offer_code, code) — идемпотентна, строка одна.
async def test_record_idempotent(pg_engine):
    async with pg_engine.begin() as conn:
        await record_creative(
            conn,
            offer_code="TST_R",
            code="TST_R_CR001",
            kind="image",
            meta_creative_id="111",
            run_id=None,
        )
        await record_creative(
            conn,
            offer_code="TST_R",
            code="TST_R_CR001",
            kind="image",
            meta_creative_id="111",
            run_id=None,
        )
        cnt = (
            await conn.execute(
                text("SELECT count(*) FROM campaign_creative WHERE offer_code='TST_R'")
            )
        ).scalar()
    assert cnt == 1


# peek на несуществующем оффере возвращает 0 (строки в таблице нет).
async def test_peek_empty_offer(pg_engine):
    async with pg_engine.begin() as conn:
        result = await peek_next_seq(conn, "TST_EMPTY")
    assert result == 0


# span<=0 не меняет счётчик, возвращает (текущий next_seq + 1).
async def test_allocate_zero_span_no_advance(pg_engine):
    async with pg_engine.begin() as conn:
        # Сначала выдаём реальный диапазон
        await allocate_code_span(conn, "TST_ZERO", 3)
        seq_before = await peek_next_seq(conn, "TST_ZERO")
        # span=0 не должен изменить счётчик
        base = await allocate_code_span(conn, "TST_ZERO", 0)
        seq_after = await peek_next_seq(conn, "TST_ZERO")
    assert seq_before == seq_after  # счётчик не изменился
    assert base == seq_before + 1  # вернул следующий за текущим
