# -*- coding: utf-8 -*-
"""Integration: мульти-кабинет M1 — scan set из офферов + ad_account_id в каталоге.

Покрывает:
1. resolve_scan_account_ids — union по активным офферам, дедуп, сортировка,
   неактивные офферы игнорируются, мусорные значения отбрасываются.
2. list_offers_without_accounts — активные офферы с пустым списком кабинетов.
3. upsert_catalog_hierarchy — требует явные account/campaign IDs и пишет
   fb_campaigns.ad_account_id.

Cleanup prefix-scoped (урок Round 11): не трогаем чужие строки при random-порядке.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.accounts import list_offers_without_accounts, resolve_scan_account_ids
from core.observer.writers import upsert_catalog_hierarchy

# Префикс изоляции данных этого модуля.
PFX = "MCAB"


@pytest_asyncio.fixture
async def clean_mcab(pg_engine: AsyncEngine):
    """Prefix-scoped cleanup офферов/каталога этого теста (до и после)."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM fb_ads WHERE ad_name LIKE :p"), {"p": f"{PFX}%"})
            await conn.execute(
                text("DELETE FROM fb_adsets WHERE adset_name LIKE :p"), {"p": f"{PFX}%"}
            )
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE campaign_name LIKE :p"), {"p": f"{PFX}%"}
            )
            await conn.execute(text("DELETE FROM offers WHERE code LIKE :p"), {"p": f"{PFX}%"})

    await _cleanup()
    yield
    await _cleanup()


async def _insert_offer(
    engine: AsyncEngine, *, code: str, accounts: list[str], is_active: bool = True
) -> None:
    """Вставка оффера с заданным списком кабинетов."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO offers (id, code, name, is_active, ad_account_ids)
                VALUES (:id, :code, :name, :act, :accs)
                """
            ),
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": f"Тест {code}",
                "act": is_active,
                "accs": accounts,
            },
        )


# Union кабинетов активных офферов: дедуп пересечений, сортировка, неактивный игнорируется.
@pytest.mark.asyncio
async def test_resolve_scan_account_ids_union(pg_engine: AsyncEngine, clean_mcab) -> None:
    await _insert_offer(pg_engine, code=f"{PFX}_A", accounts=["222", "111"])
    await _insert_offer(pg_engine, code=f"{PFX}_B", accounts=["111", "333"])
    await _insert_offer(pg_engine, code=f"{PFX}_OFF", accounts=["999"], is_active=False)

    result = await resolve_scan_account_ids(pg_engine)

    # Только кабинеты активных офферов; «999» от неактивного не попал.
    for acc in ("111", "222", "333"):
        assert acc in result
    assert "999" not in result
    # Стабильный порядок обхода — отсортировано.
    assert result == sorted(result)


# Мусор в ad_account_ids (буквы, act_-префикс) нормализуется/отбрасывается.
@pytest.mark.asyncio
async def test_resolve_scan_account_ids_normalizes(pg_engine: AsyncEngine, clean_mcab) -> None:
    await _insert_offer(pg_engine, code=f"{PFX}_C", accounts=["act_777", "garbage", ""])

    result = await resolve_scan_account_ids(pg_engine)

    assert "777" in result
    assert "garbage" not in result and "" not in result and "act_777" not in result


# Активный оффер с пустым списком кабинетов попадает в warning-список.
@pytest.mark.asyncio
async def test_list_offers_without_accounts(pg_engine: AsyncEngine, clean_mcab) -> None:
    await _insert_offer(pg_engine, code=f"{PFX}_EMPTY", accounts=[])
    await _insert_offer(pg_engine, code=f"{PFX}_FULL", accounts=["123"])

    missing = await list_offers_without_accounts(pg_engine)

    assert f"{PFX}_EMPTY" in missing
    assert f"{PFX}_FULL" not in missing


@pytest.mark.asyncio
async def test_upsert_catalog_rejects_missing_account(pg_engine: AsyncEngine, clean_mcab) -> None:
    common = dict(
        fb_ad_id="9001",
        ad_name=f"{PFX} ad",
        fb_adset_id="8101",
        adset_name=f"{PFX} adset",
        fb_campaign_id="8001",
        campaign_name=f"{PFX} campaign",
        offer_id=None,
        delivery_status="ACTIVE",
    )
    with pytest.raises(ValueError, match="explicit numeric account id"):
        await upsert_catalog_hierarchy(pg_engine, **common, ad_account_id=None)  # type: ignore[arg-type]


# Скан из другого кабинета обновляет привязку (кампания переехала/пересоздана).
@pytest.mark.asyncio
async def test_upsert_catalog_updates_account(pg_engine: AsyncEngine, clean_mcab) -> None:
    common = dict(
        fb_ad_id="9002",
        ad_name=f"{PFX} ad2",
        fb_adset_id="8102",
        adset_name=f"{PFX} adset2",
        fb_campaign_id="8002",
        campaign_name=f"{PFX} campaign2",
        offer_id=None,
        delivery_status="ACTIVE",
    )
    await upsert_catalog_hierarchy(pg_engine, **common, ad_account_id="111")
    await upsert_catalog_hierarchy(pg_engine, **common, ad_account_id="222")

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT ad_account_id FROM fb_campaigns WHERE campaign_name = :n"),
                {"n": f"{PFX} campaign2"},
            )
        ).first()
    assert row is not None and row[0] == "222"
