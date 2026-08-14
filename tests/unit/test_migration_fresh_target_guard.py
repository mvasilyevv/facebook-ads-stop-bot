from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config

from migrations.baseline_contract import BASELINE_DEFAULT_PARTITIONS, BASELINE_REVISION
from migrations.revision_guard import LinearRevisionChain, load_project_revision_chain

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run-migrations-locked.py"
SPEC = importlib.util.spec_from_file_location("run_migrations_locked", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HEAD_REVISION = load_project_revision_chain().head


class _Connection:
    def __init__(
        self,
        *,
        version_table: bool = False,
        revisions: list[str] | None = None,
        relations: list[tuple[str, str]] | None = None,
        catalog_objects: list[tuple[str, str, str]] | None = None,
        missing_sentinels: set[str] | None = None,
        artifact_rows: list[dict[str, Any]] | None = None,
        extensions: list[tuple[str, str, str, bool]] | None = None,
        partitions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.version_table = version_table
        self.revisions = revisions or []
        self.relations = relations or []
        self.catalog_objects = catalog_objects or []
        self.missing_sentinels = missing_sentinels or set()
        self.artifact_rows = artifact_rows or []
        self.extensions = extensions or [
            ("pg_catalog", "plpgsql", "1.0", False),
            *(
                [("public", "pgcrypto", "1.3", True)]
                if self.revisions and self.revisions[0] in {BASELINE_REVISION, HEAD_REVISION}
                else []
            ),
        ]
        self.partitions = (
            partitions
            or [
                {
                    "parent_schema": "public",
                    "parent_name": parent,
                    "parent_relkind": "p",
                    "parent_oid": index,
                    "child_schema": "public",
                    "child_name": child,
                    "child_relkind": "r",
                    "child_oid": index + 100,
                    "child_is_partition": True,
                    "partition_bound": "DEFAULT",
                }
                for index, (parent, child) in enumerate(
                    BASELINE_DEFAULT_PARTITIONS.items(), start=1
                )
            ]
            if self.revisions and self.revisions[0] in {BASELINE_REVISION, HEAD_REVISION}
            else []
        )
        self.sentinel_checks: list[str] = []
        self.artifact_checks = 0

    async def scalar(self, statement: object, params: dict[str, object] | None = None):
        query = str(statement)
        if "to_regclass('public.alembic_version')" in query:
            return "alembic_version" if self.version_table else None
        if "SELECT to_regclass(:relation)" in query:
            relation = str((params or {})["relation"])
            self.sentinel_checks.append(relation)
            return None if relation in self.missing_sentinels else relation
        raise AssertionError((query, params))

    async def scalars(self, statement: object):
        query = str(statement)
        if "SELECT version_num" in query:
            return list(self.revisions)
        raise AssertionError(query)

    async def execute(self, statement: object):
        query = str(statement)
        if "normalized_definition" in query:
            self.artifact_checks += 1
            return _Rows(self.artifact_rows)
        if "pg_catalog.pg_extension" in query:
            return _Rows(
                [
                    {
                        "extension_schema": schema,
                        "extension_name": name,
                        "extension_version": version,
                        "extension_relocatable": relocatable,
                    }
                    for schema, name, version, relocatable in self.extensions
                ]
            )
        if "standalone_types" in query and "standalone_collations" in query:
            return _Rows(
                [
                    {
                        "object_kind": object_kind,
                        "object_name": object_name,
                        "object_detail": object_detail,
                    }
                    for object_kind, object_name, object_detail in self.catalog_objects
                ]
            )
        if "pg_catalog.pg_inherits" in query:
            return _Rows(self.partitions)
        if "pg_catalog.pg_class" in query:
            return _Rows(
                [
                    {"relname": relation_name, "relkind": relation_kind}
                    for relation_name, relation_kind in self.relations
                ]
            )
        raise AssertionError(query)


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def __iter__(self):
        return iter(self._rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("version_table", [False, True])
async def test_fresh_target_guard_accepts_empty_database(version_table: bool) -> None:
    connection = _Connection(version_table=version_table)

    await MODULE.validate_migration_target(connection)

    assert connection.sentinel_checks == []


@pytest.mark.asyncio
async def test_fresh_target_guard_accepts_exact_installed_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(
        version_table=True,
        revisions=[HEAD_REVISION],
    )
    checked_rows: list[object] = []
    monkeypatch.setattr(
        MODULE,
        "assert_catalog_artifacts",
        lambda rows: checked_rows.extend(rows),
    )

    await MODULE.validate_migration_target(connection)

    assert connection.sentinel_checks == list(MODULE.BASELINE_RELATION_SENTINELS)
    assert connection.artifact_checks == 1
    assert checked_rows == []


@pytest.mark.asyncio
async def test_migration_target_accepts_baseline_as_known_ancestor_of_test_0002() -> None:
    connection = _Connection(
        version_table=True,
        revisions=[BASELINE_REVISION],
    )

    current = await MODULE.validate_migration_target(
        connection,
        chain=LinearRevisionChain((BASELINE_REVISION, "test_0002")),
    )

    assert current == BASELINE_REVISION
    assert connection.sentinel_checks == list(MODULE.BASELINE_RELATION_SENTINELS)
    assert connection.artifact_checks == 0


@pytest.mark.asyncio
async def test_fresh_target_guard_rejects_extra_extension_outside_public() -> None:
    connection = _Connection(
        extensions=[
            ("pg_catalog", "plpgsql", "1.0", False),
            ("audit", "hstore", "1.8", True),
        ]
    )

    with pytest.raises(RuntimeError, match="unexpected extension audit.hstore"):
        await MODULE.validate_migration_target(connection)


@pytest.mark.asyncio
async def test_migration_target_guard_rejects_unknown_revision() -> None:
    connection = _Connection(
        version_table=True,
        revisions=["legacy_revision"],
    )

    with pytest.raises(RuntimeError, match="unknown or not an ancestor"):
        await MODULE.validate_migration_target(connection)


@pytest.mark.asyncio
async def test_fresh_target_guard_rejects_unversioned_nonempty_schema() -> None:
    connection = _Connection(relations=[("offers", "r"), ("task_queue_id_seq", "S")])

    with pytest.raises(ValueError, match="unversioned non-empty.*offers"):
        await MODULE.validate_migration_target(connection)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "name", "detail"),
    [
        ("type", "legacy_state", "enum"),
        ("type", "legacy_code", "domain"),
        ("type", "legacy_pair", "composite"),
        ("type", "legacy_range", "range"),
        ("collation", "legacy_collation", "libc"),
    ],
)
async def test_fresh_target_guard_rejects_standalone_public_catalog_objects(
    kind: str,
    name: str,
    detail: str,
) -> None:
    connection = _Connection(catalog_objects=[(kind, name, detail)])

    with pytest.raises(
        ValueError,
        match=rf"standalone public catalog objects.*{name}",
    ):
        await MODULE.validate_migration_target(connection)


@pytest.mark.asyncio
async def test_installed_head_rejects_late_standalone_public_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(
        version_table=True,
        revisions=[HEAD_REVISION],
        catalog_objects=[("type", "legacy_state", "enum")],
    )
    monkeypatch.setattr(MODULE, "assert_catalog_artifacts", lambda _rows: None)

    with pytest.raises(ValueError, match="standalone.*legacy_state"):
        await MODULE.validate_migration_target(connection)


@pytest.mark.asyncio
async def test_fresh_target_guard_rejects_stamped_partial_baseline() -> None:
    missing = {"public.notification_events"}
    connection = _Connection(
        version_table=True,
        revisions=[HEAD_REVISION],
        missing_sentinels=missing,
    )

    with pytest.raises(ValueError, match="missing required baseline objects.*notification_events"):
        await MODULE.validate_migration_target(connection)

    assert connection.artifact_checks == 0


@pytest.mark.asyncio
async def test_fresh_target_guard_rejects_catalog_artifact_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(
        version_table=True,
        revisions=[HEAD_REVISION],
    )

    def _reject(_rows: object) -> None:
        raise RuntimeError(
            "safety-first baseline catalog artifact drift: "
            "missing view:public.operator_revision_state"
        )

    monkeypatch.setattr(MODULE, "assert_catalog_artifacts", _reject)

    with pytest.raises(
        RuntimeError,
        match="catalog artifact drift.*operator_revision_state",
    ):
        await MODULE.validate_migration_target(connection)


def test_alembic_commands_use_the_advisory_lock_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    connection = object()
    observed: list[tuple[str, object]] = []

    monkeypatch.setattr(
        MODULE.command,
        "upgrade",
        lambda selected, _revision: observed.append(("upgrade", selected.attributes["connection"])),
    )
    monkeypatch.setattr(
        MODULE.command,
        "current",
        lambda selected, **_kwargs: observed.append(("current", selected.attributes["connection"])),
    )
    monkeypatch.setattr(
        MODULE.command,
        "check",
        lambda selected: observed.append(("check", selected.attributes["connection"])),
    )

    MODULE._run_alembic_commands(connection, config)

    assert observed == [
        ("upgrade", connection),
        ("current", connection),
        ("check", connection),
    ]
    assert "connection" not in config.attributes


def test_migrator_uses_the_shared_forward_only_revision_contract() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "migrations.versions." not in source
    assert "load_linear_revision_chain" in source
    assert "alembic stamp" not in source
    assert "DROP SCHEMA" not in source
    assert "validate_migration_target" in source


def test_migrator_runs_upgrade_and_check_directly_under_one_lock() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker/worker-entrypoint.sh").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
    release_compose = (ROOT / "deploy/compose/docker-compose.jobs.yml").read_text(encoding="utf-8")
    base_image = (ROOT / "docker/Dockerfile.python-base").read_text(encoding="utf-8")

    assert "worker-entrypoint.sh" not in source
    assert 'command.upgrade(config, "head")' in source
    assert "command.current(config, check_heads=True)" in source
    assert "command.check(config)" in source
    assert 'config.attributes["connection"] = connection' in source
    assert "create_subprocess_exec" not in source
    assert "exec python -m scripts.run-migrations-locked" in entrypoint
    assert "$(PY) -m scripts.run-migrations-locked" in makefile
    assert "run: python -m scripts.run-migrations-locked" in workflow
    assert "run: alembic upgrade head" not in workflow
    assert 'entrypoint: ["python", "-m", "scripts.run-migrations-locked"]' in release_compose
    assert "WORKDIR /app" in base_image
    assert "COPY . ." not in base_image
    assert "COPY migrations ./migrations" in base_image
