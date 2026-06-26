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
    reconcile_offer_seq,
    record_creative,
)

pytestmark = pytest.mark.integration


async def _cleanup(conn):
    await conn.execute(text("DELETE FROM campaign_creative WHERE offer_code LIKE 'TST_%'"))
    await conn.execute(text("DELETE FROM offer_creative_seq WHERE offer_code LIKE 'TST_%'"))
    await conn.execute(text("DELETE FROM campaign_run WHERE config->>'offer_code' LIKE 'TST_%'"))


@pytest_asyncio.fixture(autouse=True)
async def clean_ledger(pg_engine):
    """Чистим тестовые строки с префиксом TST_ до и после каждого теста."""
    async with pg_engine.begin() as conn:
        await _cleanup(conn)
    yield
    async with pg_engine.begin() as conn:
        await _cleanup(conn)


async def _insert_run(conn, offer_code: str, status: str) -> str:
    """Вставляет campaign_run с config.offer_code и статусом, возвращает id."""
    rid = (
        await conn.execute(
            text(
                "INSERT INTO campaign_run (config, status) "
                "VALUES (CAST(:cfg AS JSONB), :st) RETURNING id::text"
            ),
            {"cfg": f'{{"offer_code": "{offer_code}"}}', "st": status},
        )
    ).scalar_one()
    return str(rid)


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


# reconcile опускает инфлированный next_seq к реальному максимуму из ledger.
# Сценарий: неудачные заливы сожгли span до 60, но реально создано лишь 3 креатива.
async def test_reconcile_lowers_inflated_seq(pg_engine):
    async with pg_engine.begin() as conn:
        await allocate_code_span(conn, "TST_RC", 60)  # инфляция от прошлых падений
        for i in range(1, 4):
            await record_creative(
                conn,
                offer_code="TST_RC",
                code=f"TST_RC_CR{i:03d}",
                kind="image",
                meta_creative_id=str(i),
                run_id=None,
            )
        new_seq = await reconcile_offer_seq(conn, "TST_RC")
        after = await peek_next_seq(conn, "TST_RC")
    assert new_seq == 3  # реальный максимум кода
    assert after == 3  # счётчик опущен к реальности
    # Следующий резерв продолжит с 4 (sane), а не с 61.


# reconcile НЕ трогает счётчик, если по офферу есть ДРУГОЙ незавершённый run
# (защита от затирания его зарезервированного диапазона → коллизии кодов).
async def test_reconcile_skips_when_other_inflight(pg_engine):
    async with pg_engine.begin() as conn:
        await allocate_code_span(conn, "TST_RC2", 60)
        await record_creative(
            conn,
            offer_code="TST_RC2",
            code="TST_RC2_CR001",
            kind="image",
            meta_creative_id="1",
            run_id=None,
        )
        await _insert_run(conn, "TST_RC2", "creating")  # конкурентный in-flight run
        result = await reconcile_offer_seq(conn, "TST_RC2")
        after = await peek_next_seq(conn, "TST_RC2")
    assert result is None  # пропущено
    assert after == 60  # счётчик НЕ опущен (резерв конкурента цел)


# Текущий запускаемый run (exclude_run_id) не считается конкурентом — reconcile проходит.
async def test_reconcile_excludes_current_run(pg_engine):
    async with pg_engine.begin() as conn:
        await allocate_code_span(conn, "TST_RC3", 60)
        await record_creative(
            conn,
            offer_code="TST_RC3",
            code="TST_RC3_CR002",
            kind="image",
            meta_creative_id="2",
            run_id=None,
        )
        current = await _insert_run(conn, "TST_RC3", "queued")  # это сам текущий run
        new_seq = await reconcile_offer_seq(conn, "TST_RC3", exclude_run_id=current)
        after = await peek_next_seq(conn, "TST_RC3")
    assert new_seq == 2
    assert after == 2  # опущен, т.к. текущий run исключён из проверки


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
