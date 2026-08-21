# -*- coding: utf-8 -*-
"""Новый кабинет получает снимок без единого скана.

Замкнутый круг 17.08.2026: визард требует подтверждённый контекст кабинета,
контекст писал только фоновый refresh, а refresh обходил лишь кабинеты с уже
отсканированными кампаниями. У нового кабинета их нет — и при выключенном
сканировании круг не размыкался никогда.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

import core.meta_api.account_tz as account_tz

_ACCOUNT_ID = "1234567890123456"
_OFFER_CODE = "SCOPE_TST"


class _Client:
    """Живой канал, отвечающий как настоящая Meta по кабинету оффера."""

    async def execute_graph_call(self, **_kwargs):
        return {"timezone_name": "America/Dawson_Creek", "currency": "USD"}


@pytest_asyncio.fixture
async def configured_cabinet(pg_engine):
    """Кабинет активного оффера, живущий только на время теста.

    Убирает за собой полностью: adoption-preflight соседних тестов требует
    цель без единой строки прикладных данных.
    """
    async with pg_engine.begin() as conn:
        offer_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO offers (code, name, is_active)
                    VALUES (:code, :code, TRUE)
                    RETURNING id
                    """
                ),
                {"code": _OFFER_CODE},
            )
        ).scalar_one()
        await conn.execute(
            text("INSERT INTO ad_accounts (account_id) VALUES (:account_id)"),
            {"account_id": _ACCOUNT_ID},
        )
        await conn.execute(
            text(
                """
                INSERT INTO offer_ad_accounts (offer_id, account_id)
                VALUES (:offer_id, :account_id)
                """
            ),
            {"offer_id": offer_id, "account_id": _ACCOUNT_ID},
        )
    try:
        yield _ACCOUNT_ID
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM meta_account_snapshot WHERE account_id = :account_id"),
                {"account_id": _ACCOUNT_ID},
            )
            await conn.execute(
                text("DELETE FROM offer_ad_accounts WHERE account_id = :account_id"),
                {"account_id": _ACCOUNT_ID},
            )
            await conn.execute(
                text("DELETE FROM ad_accounts WHERE account_id = :account_id"),
                {"account_id": _ACCOUNT_ID},
            )
            await conn.execute(
                text("DELETE FROM offers WHERE code = :code"),
                {"code": _OFFER_CODE},
            )


@pytest.mark.asyncio
async def test_configured_cabinet_gets_snapshot_without_any_scan(
    pg_engine,
    configured_cabinet,
) -> None:
    # Ни одной отсканированной кампании: сканирование выключено.
    async with pg_engine.connect() as conn:
        scanned = await conn.scalar(text("SELECT count(*) FROM fb_campaigns"))
    assert scanned == 0

    updated = await account_tz.refresh_account_timezones(pg_engine, _Client())

    assert updated == 1
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT timezone_name, currency
                    FROM meta_account_snapshot
                    WHERE account_id = :account_id
                    """
                ),
                {"account_id": configured_cabinet},
            )
        ).first()
    assert row is not None
    assert row.timezone_name == "America/Dawson_Creek"
    assert row.currency == "USD"
