# -*- coding: utf-8 -*-
"""Новый кабинет получает снимок без единого скана.

Замкнутый круг 17.08.2026: визард требует подтверждённый контекст кабинета,
контекст писал только фоновый refresh, а refresh обходил лишь кабинеты с уже
отсканированными кампаниями. У нового кабинета их нет — и при выключенном
сканировании круг не размыкался никогда.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

import core.meta_api.account_tz as account_tz


class _Client:
    """Живой канал, отвечающий как настоящая Meta по кабинету оффера."""

    async def execute_graph_call(self, **_kwargs):
        return {"timezone_name": "America/Dawson_Creek", "currency": "USD"}


@pytest.mark.asyncio
async def test_configured_cabinet_gets_snapshot_without_any_scan(pg_engine) -> None:
    async with pg_engine.begin() as conn:
        offer_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO offers (code, name, is_active)
                    VALUES ('SCOPE_TST', 'SCOPE_TST', TRUE)
                    RETURNING id
                    """
                )
            )
        ).scalar_one()
        await conn.execute(text("INSERT INTO ad_accounts (account_id) VALUES ('2108857220005012')"))
        await conn.execute(
            text(
                """
                INSERT INTO offer_ad_accounts (offer_id, account_id)
                VALUES (:offer_id, '2108857220005012')
                """
            ),
            {"offer_id": offer_id},
        )

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
                    WHERE account_id = '2108857220005012'
                    """
                )
            )
        ).first()
    assert row is not None
    assert row.timezone_name == "America/Dawson_Creek"
    assert row.currency == "USD"
