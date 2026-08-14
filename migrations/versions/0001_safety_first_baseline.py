# -*- coding: utf-8 -*-
"""Create the complete safety-first schema on a proven-empty PostgreSQL target.

This revision is intentionally a frozen installation artifact.  It does not
import application models and it has no legacy-upgrade or downgrade path.  The
environment and this revision both fail closed unless ``public`` is empty
(apart from Alembic's own version table).

The SQL asset was generated from the canonical PostgreSQL 16 schema.  Its
indexes and constraints are created transactionally rather than concurrently:
the empty-target guard proves there can be no application readers or writers.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from alembic import op
from sqlalchemy import text

from migrations.baseline_contract import (
    CATALOG_ARTIFACTS_SQL,
    DATABASE_EXTENSION_LAYOUT_SQL,
    PUBLIC_APPLICATION_RELATIONS_SQL,
    PUBLIC_PARTITION_LAYOUT_SQL,
    PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL,
    assert_catalog_artifacts,
    describe_public_application_relations,
    describe_standalone_public_catalog_objects,
    validate_database_extension_layout,
    validate_public_partition_layout,
)

revision = "0001_safety_first_baseline"
down_revision = None
branch_labels = None
depends_on = None

BASELINE_ASSET_SHA256 = "535830715e0e2e7269d68a4839eacef098a449d5ccb6e193dd0c1d5af4b79ce1"
BASELINE_RETENTION_POLICY: dict[str, str] = {
    "ad_metrics": "45 days",
    "alert_events": "120 days",
    "scan_runs": "30 days",
    "meta_api_audit_log": "30 days",
    "adsetpro_postback_events": "45 days",
    "task_queue_completed": "30 days",
    "task_queue_failed": "45 days",
    "adset_duplicate_previews_expired": "immediate",
    "browser_operation_capabilities_expired": "immediate",
    "telegram_invites_expired": "30 days",
    "operator_revision_events": "7 days",
    "incidents_terminal": "180 days",
    "notification_events_terminal": "90 days",
    "telegram_action_tokens_terminal": "45 days",
    "telegram_navigation_tokens_terminal": "30 days",
    "telegram_updates_terminal": "30 days",
    "telegram_command_replies_terminal": "30 days",
    "ai_cache": "redis_ttl_only",
}

_DOLLAR_TAG_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def _asset_path() -> Path:
    return Path(__file__).with_suffix(".sql")


def _load_verified_asset() -> str:
    payload = _asset_path().read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != BASELINE_ASSET_SHA256:
        raise RuntimeError(
            "safety-first baseline SQL checksum mismatch; regenerate and review "
            "the frozen migration asset instead of editing it in place"
        )
    sql = payload.decode("utf-8")
    for line_number, line in enumerate(sql.splitlines(), start=1):
        if line.lstrip().startswith("\\"):
            raise RuntimeError(
                f"psql meta-command is forbidden in the runtime baseline asset (line {line_number})"
            )
    return sql


def _split_sql(sql: str) -> list[str]:
    """Split PostgreSQL SQL without breaking functions, comments or literals."""

    statements: list[str] = []
    chunk: list[str] = []
    state = "normal"
    dollar_tag = ""
    block_depth = 0
    index = 0

    while index < len(sql):
        char = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""

        if state == "normal":
            if char == "-" and following == "-":
                chunk.extend((char, following))
                index += 2
                state = "line_comment"
                continue
            if char == "/" and following == "*":
                chunk.extend((char, following))
                index += 2
                state = "block_comment"
                block_depth = 1
                continue
            if char == "'":
                chunk.append(char)
                index += 1
                state = "single_quote"
                continue
            if char == '"':
                chunk.append(char)
                index += 1
                state = "double_quote"
                continue
            if char == "$":
                match = _DOLLAR_TAG_RE.match(sql, index)
                if match is not None:
                    dollar_tag = match.group(0)
                    chunk.append(dollar_tag)
                    index = match.end()
                    state = "dollar_quote"
                    continue
            if char == ";":
                statement = "".join(chunk).strip()
                if statement:
                    statements.append(statement)
                chunk.clear()
                index += 1
                continue
            chunk.append(char)
            index += 1
            continue

        if state == "line_comment":
            chunk.append(char)
            index += 1
            if char == "\n":
                state = "normal"
            continue

        if state == "block_comment":
            if char == "/" and following == "*":
                chunk.extend((char, following))
                index += 2
                block_depth += 1
                continue
            if char == "*" and following == "/":
                chunk.extend((char, following))
                index += 2
                block_depth -= 1
                if block_depth == 0:
                    state = "normal"
                continue
            chunk.append(char)
            index += 1
            continue

        if state == "dollar_quote":
            if sql.startswith(dollar_tag, index):
                chunk.append(dollar_tag)
                index += len(dollar_tag)
                state = "normal"
                dollar_tag = ""
                continue
            chunk.append(char)
            index += 1
            continue

        if state == "single_quote":
            chunk.append(char)
            index += 1
            if char == "\\" and index < len(sql):
                chunk.append(sql[index])
                index += 1
                continue
            if char == "'":
                if index < len(sql) and sql[index] == "'":
                    chunk.append(sql[index])
                    index += 1
                else:
                    state = "normal"
            continue

        if state == "double_quote":
            chunk.append(char)
            index += 1
            if char == '"':
                if index < len(sql) and sql[index] == '"':
                    chunk.append(sql[index])
                    index += 1
                else:
                    state = "normal"
            continue

    if state not in {"normal", "line_comment"}:
        raise ValueError(f"unterminated SQL construct: {state}")
    statement = "".join(chunk).strip()
    if statement:
        statements.append(statement)
    return statements


def _assert_empty_public_schema(connection) -> None:
    validate_database_extension_layout(
        connection.execute(text(DATABASE_EXTENSION_LAYOUT_SQL)).mappings(),
        baseline_installed=False,
    )
    found = describe_public_application_relations(
        connection.execute(text(PUBLIC_APPLICATION_RELATIONS_SQL)).mappings()
    )
    if found:
        raise RuntimeError(
            f"safety-first baseline requires an empty public schema; found {found!r}"
        )
    catalog_objects = describe_standalone_public_catalog_objects(
        connection.execute(text(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL)).mappings()
    )
    if catalog_objects:
        raise RuntimeError(
            "safety-first baseline requires no standalone public catalog objects; "
            f"found {catalog_objects!r}"
        )
    validate_public_partition_layout(
        connection.execute(text(PUBLIC_PARTITION_LAYOUT_SQL)).mappings(),
        require_baseline_defaults=False,
    )


def upgrade() -> None:
    connection = op.get_bind()
    _assert_empty_public_schema(connection)
    for statement in _split_sql(_load_verified_asset()):
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql("SELECT pg_catalog.set_config('search_path', 'public', false)")
    connection.execute(
        text(
            """
            INSERT INTO public.system_config (key, value, description)
            VALUES (
                'retention_policy',
                CAST(:value AS jsonb),
                'Retention policy for cleanup worker'
            )
            """
        ),
        {"value": json.dumps(BASELINE_RETENTION_POLICY, sort_keys=True)},
    )
    validate_database_extension_layout(
        connection.execute(text(DATABASE_EXTENSION_LAYOUT_SQL)).mappings(),
        baseline_installed=True,
    )
    catalog_objects = describe_standalone_public_catalog_objects(
        connection.execute(text(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL)).mappings(),
        allow_manifested_routines=True,
    )
    if catalog_objects:
        raise RuntimeError(
            "safety-first baseline created unexpected standalone public catalog "
            f"objects: {catalog_objects!r}"
        )
    validate_public_partition_layout(
        connection.execute(text(PUBLIC_PARTITION_LAYOUT_SQL)).mappings(),
        require_baseline_defaults=True,
    )
    assert_catalog_artifacts(connection.execute(text(CATALOG_ARTIFACTS_SQL)).mappings())


def downgrade() -> None:
    raise RuntimeError(
        "0001_safety_first_baseline is irreversible; restore from backup or "
        "replace the disposable database instead of downgrading"
    )
