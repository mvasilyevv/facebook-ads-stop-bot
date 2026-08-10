from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from core.ad_account_catalog import AdAccountCatalog, canonical_account_ids
from core.models import Base

ROOT = Path(__file__).resolve().parents[2]


def test_normalized_account_schema_has_exact_identity_and_membership_contract() -> None:
    accounts = Base.metadata.tables["ad_accounts"]
    memberships = Base.metadata.tables["offer_ad_accounts"]
    offers = Base.metadata.tables["offers"]

    assert set(accounts.columns.keys()) == {"account_id", "created_at", "updated_at"}
    assert accounts.primary_key.columns.keys() == ["account_id"]
    assert accounts.c.account_id.type.length == 32
    assert {
        constraint.name
        for constraint in accounts.constraints
        if isinstance(constraint, CheckConstraint)
    } == {"ck_ad_accounts_account_id"}

    assert memberships.primary_key.columns.keys() == ["offer_id", "account_id"]
    assert {
        (foreign_key.target_fullname, foreign_key.ondelete)
        for foreign_key in memberships.foreign_keys
    } == {
        ("offers.id", "CASCADE"),
        ("ad_accounts.account_id", "RESTRICT"),
    }
    assert {index.name for index in memberships.indexes} == {"ix_offer_ad_accounts_account_offer"}
    assert "ad_account_ids" not in offers.columns


def test_catalog_exposes_one_complete_account_membership_interface() -> None:
    for method in (
        "list_accounts",
        "list_by_offer",
        "create_accounts",
        "replace_offer_accounts",
        "resolve_scan_set",
        "list_active_offers_without_accounts",
        "offer_has_account",
    ):
        assert callable(getattr(AdAccountCatalog, method))


def test_catalog_canonicalizes_deduplicates_and_sorts_identities() -> None:
    assert canonical_account_ids(["act_222", "111", "222"]) == ("111", "222")
    with pytest.raises(ValueError, match="explicit numeric account id"):
        canonical_account_ids(["1" * 33])


def test_runtime_has_no_offer_array_or_projection_fallback() -> None:
    runtime_paths = (
        "apps/api/routers/v1/offers.py",
        "apps/api/routers/v1/campaigns_create.py",
        "apps/mcp_server/resources.py",
        "core/ai_assistant/tools/ops/get_active_offers.py",
        "core/observer/accounts.py",
    )
    forbidden = (
        "offers.ad_account_ids",
        "offer.ad_account_ids",
        "unnest(ad_account_ids)",
        "cardinality(ad_account_ids)",
        'getattr(offer, "ad_account_ids"',
        "Offer.__table__.c.ad_account_ids",
    )

    for relative_path in runtime_paths:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{token!r} returned in {relative_path}"
