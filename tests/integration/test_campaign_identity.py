# -*- coding: utf-8 -*-
"""Integration: идентичность кампании = fb_campaign_id (HIGH-3, миграция 0020).

Сценарии:
1. Одноимённые кампании РАЗНЫХ кабинетов (разные fb_campaign_id) — ДВЕ строки каталога,
   каждая со своим ad_account_id; ads цепляются каждая к своей кампании.
2. Переименование кампании (тот же fb_campaign_id, новое имя) — строка одна, имя обновлено.
3. Adoption: legacy-строка без fb_campaign_id получает ID при первом скане с ID
   (дубль не создаётся).
4. Adoption-guard: если ID уже занят другой кампанией, legacy-строка с тем же именем
   НЕ крадёт его (ID остаётся у владельца, обе строки живы).

Cleanup prefix-scoped (урок Round 11).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.writers import upsert_catalog_hierarchy

PFX = "CIDT"


@pytest_asyncio.fixture
async def clean_cidt(pg_engine: AsyncEngine):
    """Prefix-scoped cleanup каталога этого модуля (до и после)."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM fb_ads WHERE ad_name LIKE :p"), {"p": f"{PFX}%"})
            await conn.execute(
                text("DELETE FROM fb_adsets WHERE adset_name LIKE :p"), {"p": f"{PFX}%"}
            )
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE campaign_name LIKE :p"), {"p": f"{PFX}%"}
            )

    await _cleanup()
    yield
    await _cleanup()


def _common(n: int, **overrides) -> dict:
    """Шаблон аргументов upsert_catalog_hierarchy."""
    base = dict(
        fb_ad_id=f"{PFX}77{n}",
        ad_name=f"{PFX} ad{n}",
        fb_adset_id=None,
        adset_name=f"{PFX} adset{n}",
        fb_campaign_id=None,
        campaign_name=f"{PFX} campaign",
        offer_id=None,
        delivery_status="ACTIVE",
    )
    base.update(overrides)
    return base


async def _campaigns(pg_engine: AsyncEngine) -> list[tuple]:
    """Все кампании теста: (fb_campaign_id, campaign_name, ad_account_id)."""
    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT fb_campaign_id, campaign_name, ad_account_id FROM fb_campaigns "
                    "WHERE campaign_name LIKE :p ORDER BY created_at"
                ),
                {"p": f"{PFX}%"},
            )
        ).fetchall()
    return [tuple(r) for r in rows]


# Одноимённые кампании разных кабинетов НЕ сливаются: две строки, каждая со своим кабинетом.
@pytest.mark.asyncio
async def test_same_name_different_cabinets_two_rows(pg_engine: AsyncEngine, clean_cidt) -> None:
    await upsert_catalog_hierarchy(
        pg_engine, **_common(1, fb_campaign_id="9001"), ad_account_id="111"
    )
    await upsert_catalog_hierarchy(
        pg_engine, **_common(2, fb_campaign_id="9002"), ad_account_id="222"
    )

    rows = await _campaigns(pg_engine)
    assert len(rows) == 2
    assert {(r[0], r[2]) for r in rows} == {("9001", "111"), ("9002", "222")}

    # Ads прицеплены каждый к своей кампании (через adset-цепочку).
    async with pg_engine.connect() as conn:
        ad_camps = (
            await conn.execute(
                text(
                    """
                    SELECT a.fb_ad_id, c.fb_campaign_id
                    FROM fb_ads a
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE a.ad_name LIKE :p ORDER BY a.fb_ad_id
                    """
                ),
                {"p": f"{PFX}%"},
            )
        ).fetchall()
    assert [(r[0], r[1]) for r in ad_camps] == [(f"{PFX}771", "9001"), (f"{PFX}772", "9002")]


# Переименование: тот же fb_campaign_id с новым именем обновляет строку, не плодит дубль.
@pytest.mark.asyncio
async def test_rename_same_campaign_updates_row(pg_engine: AsyncEngine, clean_cidt) -> None:
    await upsert_catalog_hierarchy(
        pg_engine, **_common(1, fb_campaign_id="9001"), ad_account_id="111"
    )
    await upsert_catalog_hierarchy(
        pg_engine,
        **_common(1, fb_campaign_id="9001", campaign_name=f"{PFX} campaign RENAMED"),
        ad_account_id="111",
    )

    rows = await _campaigns(pg_engine)
    assert len(rows) == 1
    assert rows[0][1] == f"{PFX} campaign RENAMED"


# Adoption: legacy-строка без Graph ID получает его при первом скане с ID — без дубля.
@pytest.mark.asyncio
async def test_legacy_row_adopts_graph_id(pg_engine: AsyncEngine, clean_cidt) -> None:
    # Legacy-скан без ID создаёт строку с fb_campaign_id IS NULL.
    await upsert_catalog_hierarchy(pg_engine, **_common(1), ad_account_id=None)
    # Современный скан приносит Graph ID для того же имени.
    await upsert_catalog_hierarchy(
        pg_engine, **_common(1, fb_campaign_id="9009"), ad_account_id="111"
    )

    rows = await _campaigns(pg_engine)
    assert len(rows) == 1
    assert rows[0][0] == "9009" and rows[0][2] == "111"


# Adoption-guard: занятый ID не крадётся legacy-строкой с совпадающим именем.
@pytest.mark.asyncio
async def test_adoption_guard_does_not_steal_id(pg_engine: AsyncEngine, clean_cidt) -> None:
    # Кампания с ID 9001 уже существует (кабинет 111).
    await upsert_catalog_hierarchy(
        pg_engine, **_common(1, fb_campaign_id="9001"), ad_account_id="111"
    )
    # Legacy-строка с тем же именем без ID (исторический скан).
    await upsert_catalog_hierarchy(pg_engine, **_common(2), ad_account_id=None)
    assert len(await _campaigns(pg_engine)) == 2

    # Повторный скан кампании 9001: adoption-guard не должен красть ID у владельца
    # и не должен ломать upsert (UNIQUE violation).
    await upsert_catalog_hierarchy(
        pg_engine, **_common(1, fb_campaign_id="9001"), ad_account_id="111"
    )

    rows = await _campaigns(pg_engine)
    assert len(rows) == 2
    assert sum(1 for r in rows if r[0] == "9001") == 1


# Fallback без ID: повторный скан того же имени переиспользует строку (старое поведение).
@pytest.mark.asyncio
async def test_fallback_without_id_reuses_row(pg_engine: AsyncEngine, clean_cidt) -> None:
    await upsert_catalog_hierarchy(pg_engine, **_common(1), ad_account_id="111")
    await upsert_catalog_hierarchy(pg_engine, **_common(1), ad_account_id="111")

    rows = await _campaigns(pg_engine)
    assert len(rows) == 1 and rows[0][2] == "111"
