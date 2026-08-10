# -*- coding: utf-8 -*-
"""Integration: identity is scoped by ad account and Meta campaign ID.

Сценарии:
1. Одноимённые кампании РАЗНЫХ кабинетов (разные fb_campaign_id) — ДВЕ строки каталога,
   каждая со своим ad_account_id; ads цепляются каждая к своей кампании.
2. Переименование кампании (тот же fb_campaign_id, новое имя) — строка одна, имя обновлено.
3. Пустой/нечисловой fb_campaign_id отклоняется до транзакции: name-only каталог
   больше не создаётся.

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
        fb_ad_id=f"700{n}",
        ad_name=f"{PFX} ad{n}",
        fb_adset_id=f"800{n}",
        adset_name=f"{PFX} adset{n}",
        fb_campaign_id=f"900{n}",
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
    assert [(r[0], r[1]) for r in ad_camps] == [("7001", "9001"), ("7002", "9002")]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("campaign_id", [None, "", "campaign-9001", " 9001 "])
async def test_missing_or_noncanonical_campaign_id_is_rejected(
    pg_engine: AsyncEngine,
    clean_cidt,
    campaign_id: str | None,
) -> None:
    with pytest.raises(ValueError, match="canonical numeric Meta campaign id"):
        await upsert_catalog_hierarchy(
            pg_engine,
            **_common(1, fb_campaign_id=campaign_id),
            ad_account_id="111",
        )

    assert await _campaigns(pg_engine) == []
