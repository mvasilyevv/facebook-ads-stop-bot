"""Transactional orchestration for adoption export, dry-run and import."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.adoption.bundle import (
    AdoptionBundleV1,
    AdoptionSectionsV1,
    build_adoption_bundle,
)
from core.adoption.profiles import LegacySourceProfile
from core.adoption.repository import (
    AdoptionSemanticMismatchError,
    LegacyArraySourceRepository,
    NormalizedTargetRepository,
)

_EXPORT_TRANSACTION_SQL = text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
_IMPORT_TRANSACTION_SQL = text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
_TRANSACTION_STATE_SQL = text(
    """
    /* adoption:transaction-state */
    SELECT current_setting('transaction_isolation') AS isolation,
           current_setting('transaction_read_only')::boolean AS read_only
    """
)
_IMPORT_LOCK_SQL = text(
    """
    /* adoption:import-lock */
    SELECT pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext('fb-agent:adoption-bundle:v1')
    )
    """
)


class AdoptionTransactionError(RuntimeError):
    """The database did not establish the required safety transaction."""


class AdoptionImportConfirmationError(ValueError):
    """A real import lacks the exact signed source fingerprint confirmation."""


@dataclass(frozen=True)
class AdoptionImportResult:
    dry_run: bool
    source_fingerprint: str
    entity_counts: dict[str, int]
    section_sha256: dict[str, str]


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _source_locator_digest(engine: AsyncEngine) -> str:
    """Hash non-secret endpoint identity without returning its raw metadata."""

    url = engine.url
    if not url.host or not url.database:
        raise AdoptionTransactionError("source database identity is incomplete")
    canonical = json.dumps(
        {
            "database": url.database,
            "host": url.host.lower(),
            "port": url.port or 5432,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_fingerprint(
    sections: AdoptionSectionsV1,
    *,
    profile_name: str,
    source_locator_digest: str,
) -> str:
    """Bind source identity, explicit profile and semantic configuration."""

    if not _SHA256_RE.fullmatch(source_locator_digest):
        raise AdoptionTransactionError("source database identity digest is invalid")

    payload = {
        "profile": profile_name,
        "source_locator_sha256": source_locator_digest,
        "sections": sections.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _assert_transaction_state(
    conn: AsyncConnection,
    *,
    isolation: str,
    read_only: bool,
) -> None:
    row = (await conn.execute(_TRANSACTION_STATE_SQL)).mappings().one()
    if str(row["isolation"]).lower() != isolation or bool(row["read_only"]) is not read_only:
        raise AdoptionTransactionError("database transaction safety mode mismatch")


async def export_legacy_bundle(
    engine: AsyncEngine,
    *,
    profile: LegacySourceProfile,
    exported_at: datetime | None = None,
    repository_factory: Callable[
        [AsyncConnection, LegacySourceProfile], LegacyArraySourceRepository
    ] = LegacyArraySourceRepository,
) -> AdoptionBundleV1:
    """Export one exact legacy profile under a repeatable read-only snapshot."""

    locator_digest = _source_locator_digest(engine)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(_EXPORT_TRANSACTION_SQL)
            await _assert_transaction_state(
                conn,
                isolation="repeatable read",
                read_only=True,
            )
            repository = repository_factory(conn, profile)
            await repository.preflight()
            sections = await repository.project()
            bundle = build_adoption_bundle(
                sections,
                exported_at=exported_at or datetime.now(UTC),
                source_fingerprint=source_fingerprint(
                    sections,
                    profile_name=profile.name,
                    source_locator_digest=locator_digest,
                ),
            )
            await transaction.commit()
            return bundle
        except BaseException:
            if transaction.is_active:
                await transaction.rollback()
            raise


def _assert_semantic_projection(
    bundle: AdoptionBundleV1,
    sections: AdoptionSectionsV1,
) -> None:
    projected = build_adoption_bundle(
        sections,
        exported_at=bundle.exported_at,
        source_fingerprint=bundle.source_fingerprint,
    )
    if (
        projected.entity_counts != bundle.entity_counts
        or projected.section_sha256 != bundle.section_sha256
    ):
        raise AdoptionSemanticMismatchError("target semantic verification failed")


async def apply_adoption_bundle(
    engine: AsyncEngine,
    *,
    bundle: AdoptionBundleV1,
    dry_run: bool,
    confirmed_source_fingerprint: str | None = None,
    repository_factory: Callable[
        [AsyncConnection], NormalizedTargetRepository
    ] = NormalizedTargetRepository,
) -> AdoptionImportResult:
    """Execute the exact import path, committing only a confirmed real import."""

    if not dry_run and confirmed_source_fingerprint != bundle.source_fingerprint:
        raise AdoptionImportConfirmationError(
            "real import requires the exact bundle source fingerprint"
        )

    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(_IMPORT_TRANSACTION_SQL)
            await _assert_transaction_state(
                conn,
                isolation="serializable",
                read_only=False,
            )
            await conn.execute(_IMPORT_LOCK_SQL)
            repository = repository_factory(conn)
            await repository.preflight_fresh()
            await repository.import_sections(bundle.sections)
            projection = await repository.project()
            _assert_semantic_projection(bundle, projection)

            result = AdoptionImportResult(
                dry_run=dry_run,
                source_fingerprint=bundle.source_fingerprint,
                entity_counts=dict(bundle.entity_counts),
                section_sha256=dict(bundle.section_sha256),
            )
            if dry_run:
                await transaction.rollback()
            else:
                await transaction.commit()
            return result
        except BaseException:
            if transaction.is_active:
                await transaction.rollback()
            raise


__all__ = [
    "AdoptionImportConfirmationError",
    "AdoptionImportResult",
    "AdoptionTransactionError",
    "apply_adoption_bundle",
    "export_legacy_bundle",
    "source_fingerprint",
]
