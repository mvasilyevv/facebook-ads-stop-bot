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
    SCHEMA_VERSION,
    SECTION_NAMES,
    AdoptionBundleV1,
    AdoptionSectionsV1,
    build_adoption_bundle,
    canonical_bundle_sha256,
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
_SERIALIZATION_FAILURE_SQLSTATE = "40001"
_SERIALIZATION_ATTEMPTS = 3


class AdoptionTransactionError(RuntimeError):
    """The database did not establish the required safety transaction."""


class AdoptionImportConfirmationError(ValueError):
    """A real import lacks the exact source fingerprint confirmation."""


class AdoptionReceiptError(RuntimeError):
    """The database adoption receipt is missing, malformed or conflicting."""


class AdoptionReceiptMissingError(AdoptionReceiptError):
    """The migrated database has not adopted a configuration bundle yet."""


class AdoptionReceiptConflictError(AdoptionReceiptError):
    """The committed receipt belongs to a different adoption bundle."""


@dataclass(frozen=True)
class AdoptionImportResult:
    dry_run: bool
    source_fingerprint: str
    entity_counts: dict[str, int]
    section_sha256: dict[str, str]
    receipt_created: bool = False


@dataclass(frozen=True)
class AdoptionReceiptSnapshot:
    id: int
    schema_version: str
    bundle_sha256: str
    source_fingerprint: str
    entity_counts: dict[str, int]
    section_sha256: dict[str, str]
    imported_at: datetime


@dataclass(frozen=True)
class AdoptionFirstReleaseResult:
    imported: bool
    source_fingerprint: str
    entity_counts: dict[str, int]
    section_sha256: dict[str, str]


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _receipt_snapshot(row: dict[str, object]) -> AdoptionReceiptSnapshot:
    """Validate the DB row independently of PostgreSQL CHECK constraints."""

    receipt_id = row.get("id")
    schema_version = row.get("schema_version")
    bundle_sha256 = row.get("bundle_sha256")
    source_fingerprint_value = row.get("source_fingerprint")
    entity_counts = row.get("entity_counts")
    section_sha256 = row.get("section_sha256")
    imported_at = row.get("imported_at")
    if (
        receipt_id != 1
        or isinstance(receipt_id, bool)
        or schema_version != SCHEMA_VERSION
        or not isinstance(bundle_sha256, str)
        or _SHA256_RE.fullmatch(bundle_sha256) is None
        or not isinstance(source_fingerprint_value, str)
        or _SHA256_RE.fullmatch(source_fingerprint_value) is None
        or not isinstance(entity_counts, dict)
        or not all(
            isinstance(key, str)
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            for key, value in entity_counts.items()
        )
        or not isinstance(section_sha256, dict)
        or set(section_sha256) != set(SECTION_NAMES)
        or not all(
            isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None
            for value in section_sha256.values()
        )
        or not isinstance(imported_at, datetime)
        or imported_at.tzinfo is None
        or imported_at.utcoffset() is None
    ):
        raise AdoptionReceiptError("database adoption receipt is malformed")
    return AdoptionReceiptSnapshot(
        id=1,
        schema_version=schema_version,
        bundle_sha256=bundle_sha256,
        source_fingerprint=source_fingerprint_value,
        entity_counts=dict(entity_counts),
        section_sha256=dict(section_sha256),
        imported_at=imported_at,
    )


def _assert_receipt_matches_bundle(
    receipt: AdoptionReceiptSnapshot,
    bundle: AdoptionBundleV1,
) -> None:
    if (
        receipt.bundle_sha256 != canonical_bundle_sha256(bundle)
        or receipt.source_fingerprint != bundle.source_fingerprint
        or receipt.entity_counts != bundle.entity_counts
        or receipt.section_sha256 != bundle.section_sha256
    ):
        raise AdoptionReceiptConflictError(
            "database adoption receipt belongs to a different bundle"
        )


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


async def _apply_adoption_bundle_once(
    engine: AsyncEngine,
    *,
    bundle: AdoptionBundleV1,
    dry_run: bool,
    confirmed_source_fingerprint: str | None = None,
    repository_factory: Callable[
        [AsyncConnection], NormalizedTargetRepository
    ] = NormalizedTargetRepository,
) -> AdoptionImportResult:
    """Execute one complete serializable import transaction."""

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
            receipt_row = await repository.read_adoption_receipt()
            if receipt_row is not None:
                await repository.preflight_baseline()
                receipt = _receipt_snapshot(receipt_row)
                _assert_receipt_matches_bundle(receipt, bundle)
                result = AdoptionImportResult(
                    dry_run=dry_run,
                    source_fingerprint=bundle.source_fingerprint,
                    entity_counts=dict(bundle.entity_counts),
                    section_sha256=dict(bundle.section_sha256),
                    receipt_created=False,
                )
                if dry_run:
                    await transaction.rollback()
                else:
                    await transaction.commit()
                return result

            await repository.preflight_fresh()
            await repository.import_sections(bundle.sections)
            projection = await repository.project()
            _assert_semantic_projection(bundle, projection)
            await repository.write_adoption_receipt(
                schema_version=bundle.schema_version,
                bundle_sha256=canonical_bundle_sha256(bundle),
                source_fingerprint=bundle.source_fingerprint,
                entity_counts=dict(bundle.entity_counts),
                section_sha256=dict(bundle.section_sha256),
            )

            result = AdoptionImportResult(
                dry_run=dry_run,
                source_fingerprint=bundle.source_fingerprint,
                entity_counts=dict(bundle.entity_counts),
                section_sha256=dict(bundle.section_sha256),
                receipt_created=not dry_run,
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


def _is_serialization_failure(exc: BaseException) -> bool:
    """Recognize SQLSTATE 40001 through SQLAlchemy/asyncpg wrappers."""

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if getattr(current, "sqlstate", None) == _SERIALIZATION_FAILURE_SQLSTATE:
            return True
        for attribute in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


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
    """Execute the import and retry the whole transaction after SQLSTATE 40001."""

    for attempt in range(_SERIALIZATION_ATTEMPTS):
        try:
            return await _apply_adoption_bundle_once(
                engine,
                bundle=bundle,
                dry_run=dry_run,
                confirmed_source_fingerprint=confirmed_source_fingerprint,
                repository_factory=repository_factory,
            )
        except Exception as exc:
            if not _is_serialization_failure(exc) or attempt + 1 == _SERIALIZATION_ATTEMPTS:
                raise
    raise AssertionError("serialization retry loop exhausted without an outcome")


async def verify_adoption_bundle(
    engine: AsyncEngine,
    *,
    bundle: AdoptionBundleV1,
    repository_factory: Callable[
        [AsyncConnection], NormalizedTargetRepository
    ] = NormalizedTargetRepository,
) -> AdoptionImportResult:
    """Verify an already imported first-release target without mutating it."""

    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(_EXPORT_TRANSACTION_SQL)
            await _assert_transaction_state(
                conn,
                isolation="repeatable read",
                read_only=True,
            )
            repository = repository_factory(conn)
            receipt_row = await repository.read_adoption_receipt()
            if receipt_row is None:
                raise AdoptionReceiptMissingError("database adoption receipt is absent")
            await repository.preflight_baseline()
            receipt = _receipt_snapshot(receipt_row)
            _assert_receipt_matches_bundle(receipt, bundle)
            result = AdoptionImportResult(
                dry_run=False,
                source_fingerprint=bundle.source_fingerprint,
                entity_counts=dict(bundle.entity_counts),
                section_sha256=dict(bundle.section_sha256),
            )
            await transaction.commit()
            return result
        except BaseException:
            if transaction.is_active:
                await transaction.rollback()
            raise


async def adopt_first_release_bundle(
    engine: AsyncEngine,
    *,
    bundle: AdoptionBundleV1,
) -> AdoptionFirstReleaseResult:
    """Import once, or prove the exact committed receipt on every retry."""

    await apply_adoption_bundle(engine, bundle=bundle, dry_run=True)
    result = await apply_adoption_bundle(
        engine,
        bundle=bundle,
        dry_run=False,
        confirmed_source_fingerprint=bundle.source_fingerprint,
    )
    return AdoptionFirstReleaseResult(
        imported=result.receipt_created,
        source_fingerprint=result.source_fingerprint,
        entity_counts=result.entity_counts,
        section_sha256=result.section_sha256,
    )


async def inspect_adoption_receipt(
    engine: AsyncEngine,
    *,
    repository_factory: Callable[
        [AsyncConnection], NormalizedTargetRepository
    ] = NormalizedTargetRepository,
) -> AdoptionReceiptSnapshot | None:
    """Return a structurally valid receipt without requiring the original bundle."""

    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(_EXPORT_TRANSACTION_SQL)
            await _assert_transaction_state(
                conn,
                isolation="repeatable read",
                read_only=True,
            )
            repository = repository_factory(conn)
            await repository.preflight_baseline()
            receipt_row = await repository.read_adoption_receipt()
            receipt = None if receipt_row is None else _receipt_snapshot(receipt_row)
            await transaction.commit()
            return receipt
        except BaseException:
            if transaction.is_active:
                await transaction.rollback()
            raise


__all__ = [
    "AdoptionImportConfirmationError",
    "AdoptionFirstReleaseResult",
    "AdoptionImportResult",
    "AdoptionReceiptConflictError",
    "AdoptionReceiptError",
    "AdoptionReceiptMissingError",
    "AdoptionReceiptSnapshot",
    "AdoptionTransactionError",
    "adopt_first_release_bundle",
    "apply_adoption_bundle",
    "export_legacy_bundle",
    "inspect_adoption_receipt",
    "source_fingerprint",
    "verify_adoption_bundle",
]
