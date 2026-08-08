from __future__ import annotations

from pathlib import Path

import pytest

from scripts import apply_schema


def test_destructive_schema_url_is_normalized_once_for_asyncpg() -> None:
    value = apply_schema._validated_database_url(  # noqa: SLF001
        "postgresql://operator:p%40ss@db.internal/fb_agent"
    )

    assert value == "postgresql+asyncpg://operator:p%40ss@db.internal:5432/fb_agent"


def test_disposable_target_requires_dedicated_marker_and_typed_local_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "postgresql+asyncpg://operator:p%40ss@127.0.0.1:5433/fb_agent_test"
    monkeypatch.setenv(
        apply_schema.DESTRUCTIVE_RESET_ENV,
        apply_schema.DESTRUCTIVE_RESET_CONFIRMATION,
    )

    assert (
        apply_schema._validate_disposable_target(  # noqa: SLF001
            url,
            confirmed_database="fb_agent_test",
        )
        == url
    )


@pytest.mark.parametrize(
    ("url", "confirmation"),
    [
        ("postgresql+asyncpg://u:p@db.internal/fb_agent_test", "fb_agent_test"),
        ("postgresql+asyncpg://u:p@127.0.0.1/fb_agent", "fb_agent"),
        ("postgresql+asyncpg://u:p@127.0.0.1/fb_agent_test", "another_test"),
    ],
)
def test_disposable_target_rejects_remote_unsuffixed_or_mismatched_database(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    confirmation: str,
) -> None:
    monkeypatch.setenv(
        apply_schema.DESTRUCTIVE_RESET_ENV,
        apply_schema.DESTRUCTIVE_RESET_CONFIRMATION,
    )
    with pytest.raises(RuntimeError):
        apply_schema._validate_disposable_target(  # noqa: SLF001
            url,
            confirmed_database=confirmation,
        )


def test_disposable_dsn_never_reads_runtime_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(apply_schema.DISPOSABLE_DATABASE_URL_ENV, raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://prod:secret@prod.internal/production",
    )
    with pytest.raises(RuntimeError, match=apply_schema.DISPOSABLE_DATABASE_URL_ENV):
        apply_schema._get_disposable_database_url()  # noqa: SLF001


@pytest.mark.parametrize(
    "value",
    [
        "sqlite:///tmp.db",
        "postgresql+psycopg://u:p@db/app",
        "postgresql://db/app",
        "postgresql://u:p@db",
    ],
)
def test_destructive_schema_url_rejects_ambiguous_or_wrong_targets(value: str) -> None:
    with pytest.raises(RuntimeError):
        apply_schema._validated_database_url(value)  # noqa: SLF001


@pytest.mark.asyncio
async def test_alembic_receives_the_exact_drop_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Process:
        async def wait(self) -> int:
            return 0

    async def _create(*args: object, **kwargs: object) -> _Process:
        captured["args"] = args
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        return _Process()

    monkeypatch.setattr(apply_schema.asyncio, "create_subprocess_exec", _create)
    url = "postgresql+asyncpg://operator:p%40ss@db.internal:5432/fb_agent"

    expected_cwd = apply_schema.PROJECT_ROOT
    assert await apply_schema._upgrade_head(url) == 0  # noqa: SLF001
    assert captured["cwd"] == expected_cwd
    assert captured["env"][apply_schema.MIGRATION_DATABASE_URL_ENV] == url  # type: ignore[index]


@pytest.mark.asyncio
async def test_disposable_reset_recreates_canonical_public_schema_acl() -> None:
    statements: list[str] = []

    class _Connection:
        async def execute(self, statement: object) -> None:
            statements.append(str(statement))

    class _Transaction:
        async def __aenter__(self) -> _Connection:
            return _Connection()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _Engine:
        def begin(self) -> _Transaction:
            return _Transaction()

    await apply_schema._drop_and_recreate_schema(_Engine())  # type: ignore[arg-type]  # noqa: SLF001

    assert statements == [
        "DROP SCHEMA IF EXISTS public CASCADE",
        "CREATE SCHEMA public AUTHORIZATION pg_database_owner",
        "GRANT USAGE ON SCHEMA public TO PUBLIC",
    ]
    assert all("GRANT ALL" not in statement for statement in statements)


def test_alembic_environment_honours_only_the_explicit_migration_override() -> None:
    source = (Path(__file__).resolve().parents[2] / "migrations" / "env.py").read_text()

    assert 'os.environ.get("FB_AGENT_MIGRATION_DATABASE_URL")' in source
    assert '_migration_database_url().replace("%", "%%")' in source


@pytest.mark.asyncio
async def test_removed_init_if_empty_alias_cannot_bootstrap_or_stamp() -> None:
    with pytest.raises(SystemExit):
        await apply_schema.main(["--init-if-empty"])


def test_apply_schema_contains_no_legacy_bootstrap_path() -> None:
    source = Path(apply_schema.__file__).read_text(encoding="utf-8")
    assert "def init_if_empty" not in source
    assert "alembic stamp" not in source
    assert "Base.metadata.create_all" not in source
    assert "CREATE EXTENSION" not in source
    assert 'os.environ.get("DATABASE_URL")' not in source
    assert "env_file.read_text" not in source
    assert "POSTGRES_PASSWORD" not in source


def test_integration_database_is_created_only_from_template0() -> None:
    source = (Path(__file__).resolve().parents[2] / "tests/integration/conftest.py").read_text(
        encoding="utf-8"
    )

    assert 'CREATE DATABASE "{db_name}" TEMPLATE template0' in source
