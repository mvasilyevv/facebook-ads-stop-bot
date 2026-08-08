"""Fail-closed contracts for destructive integration database bootstrap."""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg

from tests.integration.conftest import (
    _assert_disposable_test_target,
    _db_name_from_url,
    _integration_db_lock_identity,
    _maintenance_database_url,
    _parsed_database_url,
)


def test_database_identity_ignores_path_shaped_query_values() -> None:
    plain_query = (
        "postgresql+asyncpg://test:test@127.0.0.1/"
        "fb_agent_safety_test_query?sslrootcert=/tmp/ca.pem"
    )
    encoded_query = (
        "postgresql+asyncpg://test:test@127.0.0.1/"
        "fb_agent_safety_test_query?sslrootcert=%2Ftmp%2Fca.pem"
    )

    assert _db_name_from_url(plain_query) == "fb_agent_safety_test_query"
    assert _integration_db_lock_identity(plain_query) == _integration_db_lock_identity(
        encoded_query
    )
    maintenance = _maintenance_database_url(plain_query)
    assert maintenance.database == "postgres"
    assert maintenance.query["sslrootcert"] == "/tmp/ca.pem"


def test_percent_encoded_database_text_cannot_bypass_name_guard() -> None:
    encoded_database = (
        "postgresql+asyncpg://test:test@127.0.0.1/fb%5Fagent%5Fsafety%5Ftest%5Fencoded"
    )

    assert _db_name_from_url(encoded_database) == "fb%5Fagent%5Fsafety%5Ftest%5Fencoded"
    with pytest.raises(RuntimeError, match="lowercase ASCII"):
        _assert_disposable_test_target(encoded_database)


def test_canonical_url_matches_effective_asyncpg_target() -> None:
    parsed = _parsed_database_url(
        "postgresql://test:p%2Fss@127.0.0.1/"
        "fb_agent_safety_test_effective?prepared_statement_cache_size=0"
    )
    positional, connect_args = PGDialect_asyncpg().create_connect_args(parsed)

    assert positional == []
    assert {
        key: connect_args.get(key) for key in ("host", "port", "database", "user", "password")
    } == {
        "host": "127.0.0.1",
        "port": 5432,
        "database": "fb_agent_safety_test_effective",
        "user": "test",
        "password": "p/ss",
    }


@pytest.mark.parametrize(
    "query",
    [
        "host=evil.invalid",
        "port=6432",
        "database=prod",
        "dbname=prod",
        "user=other",
        "username=other",
        "password=other",
        "dsn=postgresql%3A%2F%2Fevil.invalid%2Fprod",
        "host=one%3A5432&host=two%3A5432",
    ],
)
def test_asyncpg_target_query_overrides_are_rejected(query: str) -> None:
    with pytest.raises(RuntimeError, match="query cannot override"):
        _parsed_database_url(
            f"postgresql+asyncpg://test:test@127.0.0.1/fb_agent_safety_test_override?{query}"
        )


def test_multi_host_authority_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="multi-host"):
        _parsed_database_url(
            "postgresql+asyncpg://test:test@127.0.0.1,localhost/fb_agent_safety_test_multihost"
        )


@pytest.mark.parametrize(
    "database",
    ["prod", "fb_agent_safety_review", "fb_agent_safety_tested_copy"],
)
def test_arbitrary_database_name_is_rejected_before_reset(database: str) -> None:
    with pytest.raises(RuntimeError, match="Integration database must"):
        _assert_disposable_test_target(f"postgresql+asyncpg://test:test@127.0.0.1/{database}")


@pytest.mark.parametrize(
    "database",
    [
        "FB_AGENT_SAFETY_TEST_UPPER",
        "fb-agent-safety-test-hyphen",
        "fb_agent_safety_test_quote'",
        "fb_agent_safety_test_кириллица",
        "f" * 64 + "_test",
    ],
)
def test_disposable_database_name_requires_strict_ascii_identifier(database: str) -> None:
    with pytest.raises(RuntimeError, match="lowercase ASCII"):
        _assert_disposable_test_target(f"postgresql+asyncpg://test:test@127.0.0.1/{database}")


@pytest.mark.parametrize(
    "database",
    ["fb_agent_safety_test_review", "fb_stop_bot_test"],
)
def test_unmistakably_disposable_local_database_is_accepted(
    database: str,
    monkeypatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DB", "runtime_database")
    _assert_disposable_test_target(f"postgresql+asyncpg://test:test@127.0.0.1/{database}")


def test_runtime_database_match_has_no_destructive_bypass(monkeypatch) -> None:
    database = "fb_agent_safety_test_runtime"
    monkeypatch.setenv("POSTGRES_DB", database)
    monkeypatch.setenv("ALLOW_PROD_DB_TESTS", "1")

    with pytest.raises(RuntimeError, match="there is no destructive-test bypass"):
        _assert_disposable_test_target(f"postgresql+asyncpg://test:test@127.0.0.1/{database}")


def test_remote_database_is_rejected_even_with_a_test_name(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_DB", "runtime_database")

    with pytest.raises(RuntimeError, match="only on loopback"):
        _assert_disposable_test_target(
            "postgresql+asyncpg://test:test@db.example.com/fb_agent_safety_test_remote"
        )
