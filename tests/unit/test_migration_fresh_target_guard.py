from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from migrations.baseline_contract import BASELINE_DEFAULT_PARTITIONS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run-migrations-locked.py"
SPEC = importlib.util.spec_from_file_location("run_migrations_locked", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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
                if self.revisions == [MODULE.BASELINE_REVISION]
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
            if self.revisions == [MODULE.BASELINE_REVISION]
            else []
        )
        self.sentinel_checks: list[str] = []
        self.artifact_checks = 0

    async def fetchval(self, query: str, *args: object):
        if "to_regclass('public.alembic_version')" in query:
            return "alembic_version" if self.version_table else None
        if query == "SELECT to_regclass($1)":
            relation = str(args[0])
            self.sentinel_checks.append(relation)
            return None if relation in self.missing_sentinels else relation
        raise AssertionError((query, args))

    async def fetch(self, query: str):
        if "SELECT version_num" in query:
            return [{"version_num": revision} for revision in self.revisions]
        if "normalized_definition" in query:
            self.artifact_checks += 1
            return self.artifact_rows
        if "pg_catalog.pg_extension" in query:
            return [
                {
                    "extension_schema": schema,
                    "extension_name": name,
                    "extension_version": version,
                    "extension_relocatable": relocatable,
                }
                for schema, name, version, relocatable in self.extensions
            ]
        if "standalone_types" in query and "standalone_collations" in query:
            return [
                {
                    "object_kind": object_kind,
                    "object_name": object_name,
                    "object_detail": object_detail,
                }
                for object_kind, object_name, object_detail in self.catalog_objects
            ]
        if "pg_catalog.pg_inherits" in query:
            return self.partitions
        if "pg_catalog.pg_class" in query:
            return [
                {"relname": relation_name, "relkind": relation_kind}
                for relation_name, relation_kind in self.relations
            ]
        raise AssertionError(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("version_table", [False, True])
async def test_fresh_target_guard_accepts_empty_database(version_table: bool) -> None:
    connection = _Connection(version_table=version_table)

    await MODULE.validate_fresh_install_target(connection)

    assert connection.sentinel_checks == []


@pytest.mark.asyncio
async def test_fresh_target_guard_accepts_exact_installed_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(
        version_table=True,
        revisions=[MODULE.BASELINE_REVISION],
    )
    checked_rows: list[object] = []
    monkeypatch.setattr(
        MODULE,
        "assert_catalog_artifacts",
        lambda rows: checked_rows.extend(rows),
    )

    await MODULE.validate_fresh_install_target(connection)

    assert connection.sentinel_checks == list(MODULE.BASELINE_RELATION_SENTINELS)
    assert connection.artifact_checks == 1
    assert checked_rows == []


@pytest.mark.asyncio
async def test_fresh_target_guard_rejects_extra_extension_outside_public() -> None:
    connection = _Connection(
        extensions=[
            ("pg_catalog", "plpgsql", "1.0", False),
            ("audit", "hstore", "1.8", True),
        ]
    )

    with pytest.raises(RuntimeError, match="unexpected extension audit.hstore"):
        await MODULE.validate_fresh_install_target(connection)


@pytest.mark.asyncio
async def test_fresh_target_guard_rejects_historical_revision() -> None:
    connection = _Connection(
        version_table=True,
        revisions=["legacy_revision"],
    )

    with pytest.raises(ValueError, match="fresh-install-only.*historical target"):
        await MODULE.validate_fresh_install_target(connection)


@pytest.mark.asyncio
async def test_fresh_target_guard_rejects_unversioned_nonempty_schema() -> None:
    connection = _Connection(relations=[("offers", "r"), ("task_queue_id_seq", "S")])

    with pytest.raises(ValueError, match="unversioned non-empty.*offers"):
        await MODULE.validate_fresh_install_target(connection)


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
        await MODULE.validate_fresh_install_target(connection)


@pytest.mark.asyncio
async def test_installed_baseline_rejects_late_standalone_public_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(
        version_table=True,
        revisions=[MODULE.BASELINE_REVISION],
        catalog_objects=[("type", "legacy_state", "enum")],
    )
    monkeypatch.setattr(MODULE, "assert_catalog_artifacts", lambda _rows: None)

    with pytest.raises(ValueError, match="standalone.*legacy_state"):
        await MODULE.validate_fresh_install_target(connection)


@pytest.mark.asyncio
async def test_fresh_target_guard_rejects_stamped_partial_baseline() -> None:
    missing = {"public.notification_events"}
    connection = _Connection(
        version_table=True,
        revisions=[MODULE.BASELINE_REVISION],
        missing_sentinels=missing,
    )

    with pytest.raises(ValueError, match="claims the safety-first baseline.*notification_events"):
        await MODULE.validate_fresh_install_target(connection)

    assert connection.artifact_checks == 0


@pytest.mark.asyncio
async def test_fresh_target_guard_rejects_catalog_artifact_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(
        version_table=True,
        revisions=[MODULE.BASELINE_REVISION],
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
        await MODULE.validate_fresh_install_target(connection)


@pytest.mark.asyncio
async def test_migration_lock_monitor_fails_when_owning_session_closes() -> None:
    class _ClosedConnection:
        def is_closed(self) -> bool:
            return True

        async def fetchval(self, _query: str) -> int:
            raise AssertionError("closed connection must not be queried")

    with pytest.raises(ConnectionError, match="advisory-lock connection closed"):
        await MODULE.monitor_lock_connection(  # type: ignore[arg-type]
            _ClosedConnection(), interval_seconds=0
        )


def test_migrator_contains_no_historical_schema_audit_or_conversion_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "migrations.versions." not in source
    assert "ScriptDirectory" not in source
    assert "alembic stamp" not in source
    assert "DROP SCHEMA" not in source
    assert "validate_fresh_install_target" in source


def test_migrator_runs_upgrade_and_check_directly_under_one_lock() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker/worker-entrypoint.sh").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    release_compose = (ROOT / "deploy/compose/docker-compose.app.yml").read_text(encoding="utf-8")
    base_image = (ROOT / "docker/Dockerfile.python-base").read_text(encoding="utf-8")

    assert "worker-entrypoint.sh" not in source
    assert 'for alembic_args in (("upgrade", "head"), ("check",))' in source
    assert "monitor_lock_connection(connection)" in source
    assert 'environment["FB_AGENT_MIGRATION_DATABASE_URL"] = database_url' in source
    assert "exec python -m scripts.run-migrations-locked" in entrypoint
    assert "$(PY) -m scripts.run-migrations-locked" in makefile
    assert "run: python -m scripts.run-migrations-locked" in workflow
    assert "run: alembic upgrade head" not in workflow
    assert 'entrypoint: ["python", "-m", "scripts.run-migrations-locked"]' in release_compose
    assert "WORKDIR /app" in base_image
    assert "COPY . ." in base_image
