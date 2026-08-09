# -*- coding: utf-8 -*-
"""Integration: мульти-кабинет M1 — scan set из офферов + ad_account_id в каталоге.

Покрывает:
1. resolve_scan_account_ids — union по нормализованным связям активных офферов,
   дедуп, сортировка; неактивные офферы игнорируются.
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from core.ad_account_catalog import ad_account_catalog
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
    """Insert an offer and replace its normalized membership in one transaction."""
    async with engine.begin() as conn:
        offer_id = (
            await conn.execute(
                text(
                    """
                INSERT INTO offers (id, code, name, is_active)
                VALUES (:id, :code, :name, :act)
                RETURNING id
                """
                ),
                {
                    "id": uuid.uuid4(),
                    "code": code,
                    "name": f"Тест {code}",
                    "act": is_active,
                },
            )
        ).scalar_one()
        await ad_account_catalog.replace_offer_accounts(
            conn,
            offer_id=offer_id,
            account_ids=accounts,
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


@pytest.mark.asyncio
async def test_replace_is_atomic_when_an_account_id_is_invalid(
    pg_engine: AsyncEngine,
    clean_mcab,
) -> None:
    await _insert_offer(pg_engine, code=f"{PFX}_C", accounts=["777"])

    with pytest.raises(ValueError, match="explicit numeric account id"):
        async with pg_engine.begin() as conn:
            offer_id = await conn.scalar(
                text("SELECT id FROM offers WHERE code = :code"),
                {"code": f"{PFX}_C"},
            )
            await ad_account_catalog.replace_offer_accounts(
                conn,
                offer_id=offer_id,
                account_ids=["888", "garbage"],
            )

    async with pg_engine.connect() as conn:
        accounts = await ad_account_catalog.list_by_offer(conn, offer_ids=[offer_id])
    assert accounts[offer_id] == ["777"]


@pytest.mark.asyncio
async def test_replace_rolls_back_deleted_links_when_insert_fails(
    pg_engine: AsyncEngine,
    clean_mcab,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _insert_offer(pg_engine, code=f"{PFX}_ROLLBACK", accounts=["778"])

    async def _return_missing_account(conn, account_ids):
        return ("779",)

    monkeypatch.setattr(ad_account_catalog, "create_accounts", _return_missing_account)
    with pytest.raises(IntegrityError):
        async with pg_engine.begin() as conn:
            offer_id = await conn.scalar(
                text("SELECT id FROM offers WHERE code = :code"),
                {"code": f"{PFX}_ROLLBACK"},
            )
            await ad_account_catalog.replace_offer_accounts(
                conn,
                offer_id=offer_id,
                account_ids=["779"],
            )

    async with pg_engine.connect() as conn:
        accounts = await ad_account_catalog.list_by_offer(conn, offer_ids=[offer_id])
    assert accounts[offer_id] == ["778"]


@pytest.mark.asyncio
async def test_database_rejects_invalid_account_identity(
    pg_engine: AsyncEngine,
    clean_mcab,
) -> None:
    with pytest.raises(IntegrityError):
        async with pg_engine.begin() as conn:
            await conn.execute(text("INSERT INTO ad_accounts (account_id) VALUES ('act_777')"))


@pytest.mark.asyncio
async def test_membership_rejects_missing_account_fk(
    pg_engine: AsyncEngine,
    clean_mcab,
) -> None:
    async with pg_engine.begin() as conn:
        offer_id = (
            await conn.execute(
                text("INSERT INTO offers (code, name) VALUES (:code, :code) RETURNING id"),
                {"code": f"{PFX}_FK"},
            )
        ).scalar_one()

    with pytest.raises(IntegrityError):
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO offer_ad_accounts (offer_id, account_id) "
                    "VALUES (:offer_id, '98765432101234567890')"
                ),
                {"offer_id": offer_id},
            )


@pytest.mark.asyncio
async def test_account_delete_is_restricted_while_linked(
    pg_engine: AsyncEngine,
    clean_mcab,
) -> None:
    await _insert_offer(pg_engine, code=f"{PFX}_RESTRICT", accounts=["654321"])

    with pytest.raises(IntegrityError):
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM ad_accounts WHERE account_id = '654321'"))


@pytest.mark.asyncio
async def test_offer_delete_cascades_membership_but_preserves_account(
    pg_engine: AsyncEngine,
    clean_mcab,
) -> None:
    await _insert_offer(pg_engine, code=f"{PFX}_ORPHAN", accounts=["654322"])

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM offers WHERE code = :code"),
            {"code": f"{PFX}_ORPHAN"},
        )
    async with pg_engine.connect() as conn:
        accounts = await ad_account_catalog.list_accounts(conn)
        membership_exists = await conn.scalar(
            text("SELECT 1 FROM offer_ad_accounts WHERE account_id = '654322'")
        )

    assert "654322" in accounts
    assert membership_exists is None


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
