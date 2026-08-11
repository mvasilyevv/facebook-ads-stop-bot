from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.engine import make_url

import core.adoption.service as adoption_service
from apps.cleanup_worker.retention import get_default_policy
from core.adoption.bundle import (
    AdoptionAccountV1,
    AdoptionObserverSettingsV1,
    AdoptionOfferRuleV1,
    AdoptionOfferV1,
    AdoptionRecipientV1,
    AdoptionSectionsV1,
    AdoptionSystemSettingsV1,
    build_adoption_bundle,
    canonical_bundle_sha256,
)
from core.adoption.profiles import get_source_profile
from core.adoption.service import (
    AdoptionImportConfirmationError,
    AdoptionImportResult,
    AdoptionReceiptConflictError,
    adopt_first_release_bundle,
    apply_adoption_bundle,
    export_legacy_bundle,
    verify_adoption_bundle,
)


def _sections(*, offer_name: str = "GH_CR2") -> AdoptionSectionsV1:
    return AdoptionSectionsV1(
        accounts=[AdoptionAccountV1(account_id="111")],
        offers=[
            AdoptionOfferV1(
                code="GH_CR2",
                name=offer_name,
                is_active=True,
                account_ids=["111"],
            )
        ],
        offer_rules=[
            AdoptionOfferRuleV1(
                offer_code="GH_CR2",
                cpa_threshold="3.00",
                currency="USD",
                frequency_threshold=None,
                stop_percent_of_rule="80",
                warning_percent_of_stop="80",
            )
        ],
        observer_settings=AdoptionObserverSettingsV1(
            interval_seconds=30,
            campaign_ids=["9001"],
        ),
        recipients=[
            AdoptionRecipientV1(
                chat_id=42,
                telegram_user_id=42,
                role="owner",
            )
        ],
        system_settings=AdoptionSystemSettingsV1(
            retention_policy=get_default_policy(),
            web_app_url=None,
        ),
    )


def _bundle():
    return build_adoption_bundle(
        _sections(),
        exported_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        source_fingerprint="a" * 64,
    )


class _Result:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def mappings(self) -> "_Result":
        return self

    def one(self) -> dict[str, Any]:
        return self._row


class _Transaction:
    def __init__(self) -> None:
        self.is_active = True
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        self.is_active = False
        self.committed = True

    async def rollback(self) -> None:
        self.is_active = False
        self.rolled_back = True


class _Connection:
    def __init__(self, *, export: bool) -> None:
        self.export = export
        self.transaction = _Transaction()
        self.statements: list[str] = []

    async def begin(self) -> _Transaction:
        return self.transaction

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "adoption:transaction-state" in sql:
            return _Result(
                {
                    "isolation": "repeatable read" if self.export else "serializable",
                    "read_only": self.export,
                }
            )
        return _Result({})


class _ConnectContext:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Connection:
        return self.conn

    async def __aexit__(self, *args) -> bool:
        return False


class _Engine:
    def __init__(
        self,
        *,
        export: bool,
        source_url: str = (
            "postgresql+asyncpg://operator:never-export@source.internal:5432/source_db"
        ),
    ) -> None:
        self.conn = _Connection(export=export)
        self.url = make_url(source_url)

    def connect(self) -> _ConnectContext:
        return _ConnectContext(self.conn)


class _SourceRepository:
    def __init__(self, conn, profile) -> None:
        self.preflight_called = False

    async def preflight(self) -> None:
        self.preflight_called = True

    async def project(self) -> AdoptionSectionsV1:
        return _sections()


class _TargetRepository:
    projection = _sections()
    fail_import = False
    events: list[str] = []
    receipt: dict[str, Any] | None = None

    def __init__(self, conn) -> None:
        pass

    async def preflight_fresh(self) -> None:
        self.events.append("preflight")

    async def preflight_baseline(self) -> None:
        self.events.append("preflight_baseline")

    async def read_adoption_receipt(self) -> dict[str, Any] | None:
        self.events.append("read_receipt")
        return self.receipt

    async def write_adoption_receipt(self, **values: Any) -> None:
        self.events.append("write_receipt")

    async def import_sections(self, sections: AdoptionSectionsV1) -> None:
        self.events.append("import")
        if self.fail_import:
            raise RuntimeError("synthetic mid-import failure with dsn=secret")

    async def project(self) -> AdoptionSectionsV1:
        self.events.append("project")
        return self.projection


def _receipt() -> dict[str, Any]:
    bundle = _bundle()
    return {
        "id": 1,
        "schema_version": bundle.schema_version,
        "bundle_sha256": canonical_bundle_sha256(bundle),
        "source_fingerprint": bundle.source_fingerprint,
        "entity_counts": dict(bundle.entity_counts),
        "section_sha256": dict(bundle.section_sha256),
        "imported_at": datetime(2026, 8, 9, 12, 1, tzinfo=UTC),
    }


@pytest.mark.asyncio
async def test_export_is_repeatable_read_only_and_commits_snapshot() -> None:
    engine = _Engine(export=True)

    bundle = await export_legacy_bundle(
        engine,  # type: ignore[arg-type]
        profile=get_source_profile("legacy-array-0036-no-preferences"),
        exported_at=datetime(2026, 8, 9, tzinfo=UTC),
        repository_factory=_SourceRepository,  # type: ignore[arg-type]
    )

    assert bundle.sections == _sections()
    assert engine.conn.transaction.committed is True
    assert engine.conn.transaction.rolled_back is False
    assert "REPEATABLE READ, READ ONLY" in engine.conn.statements[0]
    serialized = bundle.model_dump_json()
    assert "source.internal" not in serialized
    assert "source_db" not in serialized
    assert "never-export" not in serialized


@pytest.mark.asyncio
async def test_source_fingerprint_is_stable_and_binds_source_endpoint() -> None:
    profile = get_source_profile("legacy-array-0036-no-preferences")
    first = await export_legacy_bundle(
        _Engine(export=True),  # type: ignore[arg-type]
        profile=profile,
        exported_at=datetime(2026, 8, 9, tzinfo=UTC),
        repository_factory=_SourceRepository,  # type: ignore[arg-type]
    )
    repeated = await export_legacy_bundle(
        _Engine(export=True),  # type: ignore[arg-type]
        profile=profile,
        exported_at=datetime(2026, 8, 10, tzinfo=UTC),
        repository_factory=_SourceRepository,  # type: ignore[arg-type]
    )
    rotated_credentials = await export_legacy_bundle(
        _Engine(  # type: ignore[arg-type]
            export=True,
            source_url=(
                "postgresql+asyncpg://different-user:rotated@source.internal:5432/source_db"
            ),
        ),
        profile=profile,
        exported_at=datetime(2026, 8, 9, tzinfo=UTC),
        repository_factory=_SourceRepository,  # type: ignore[arg-type]
    )
    another_source = await export_legacy_bundle(
        _Engine(  # type: ignore[arg-type]
            export=True,
            source_url=("postgresql+asyncpg://operator:rotated@source.internal:5432/other_db"),
        ),
        profile=profile,
        exported_at=datetime(2026, 8, 9, tzinfo=UTC),
        repository_factory=_SourceRepository,  # type: ignore[arg-type]
    )

    assert repeated.source_fingerprint == first.source_fingerprint
    assert rotated_credentials.source_fingerprint == first.source_fingerprint
    assert another_source.source_fingerprint != first.source_fingerprint


@pytest.mark.asyncio
async def test_dry_run_executes_exact_path_and_always_rolls_back() -> None:
    engine = _Engine(export=False)
    _TargetRepository.events = []
    _TargetRepository.fail_import = False
    _TargetRepository.projection = _sections()
    _TargetRepository.receipt = None

    result = await apply_adoption_bundle(
        engine,  # type: ignore[arg-type]
        bundle=_bundle(),
        dry_run=True,
        repository_factory=_TargetRepository,  # type: ignore[arg-type]
    )

    assert result.dry_run is True
    assert _TargetRepository.events == [
        "read_receipt",
        "preflight",
        "import",
        "project",
        "write_receipt",
    ]
    assert engine.conn.transaction.rolled_back is True
    assert engine.conn.transaction.committed is False
    assert "SERIALIZABLE" in engine.conn.statements[0]
    assert any("pg_advisory_xact_lock" in sql for sql in engine.conn.statements)


@pytest.mark.asyncio
async def test_real_import_commits_only_with_exact_source_fingerprint() -> None:
    engine = _Engine(export=False)
    _TargetRepository.events = []
    _TargetRepository.fail_import = False
    _TargetRepository.projection = _sections()
    _TargetRepository.receipt = None

    with pytest.raises(AdoptionImportConfirmationError):
        await apply_adoption_bundle(
            engine,  # type: ignore[arg-type]
            bundle=_bundle(),
            dry_run=False,
            confirmed_source_fingerprint="b" * 64,
            repository_factory=_TargetRepository,  # type: ignore[arg-type]
        )
    assert _TargetRepository.events == []

    result = await apply_adoption_bundle(
        engine,  # type: ignore[arg-type]
        bundle=_bundle(),
        dry_run=False,
        confirmed_source_fingerprint="a" * 64,
        repository_factory=_TargetRepository,  # type: ignore[arg-type]
    )
    assert result.dry_run is False
    assert engine.conn.transaction.committed is True


@pytest.mark.asyncio
async def test_mid_import_failure_rolls_back_everything() -> None:
    engine = _Engine(export=False)
    _TargetRepository.events = []
    _TargetRepository.fail_import = True
    _TargetRepository.receipt = None

    with pytest.raises(RuntimeError, match="synthetic mid-import"):
        await apply_adoption_bundle(
            engine,  # type: ignore[arg-type]
            bundle=_bundle(),
            dry_run=True,
            repository_factory=_TargetRepository,  # type: ignore[arg-type]
        )

    assert engine.conn.transaction.rolled_back is True
    assert engine.conn.transaction.committed is False


@pytest.mark.asyncio
async def test_semantic_verification_mismatch_rolls_back() -> None:
    engine = _Engine(export=False)
    _TargetRepository.events = []
    _TargetRepository.fail_import = False
    _TargetRepository.projection = _sections(offer_name="tampered")
    _TargetRepository.receipt = None

    with pytest.raises(RuntimeError, match="semantic verification"):
        await apply_adoption_bundle(
            engine,  # type: ignore[arg-type]
            bundle=_bundle(),
            dry_run=True,
            repository_factory=_TargetRepository,  # type: ignore[arg-type]
        )

    assert engine.conn.transaction.rolled_back is True


@pytest.mark.asyncio
async def test_already_imported_target_is_verified_read_only() -> None:
    engine = _Engine(export=True)
    _TargetRepository.events = []
    _TargetRepository.projection = _sections()
    _TargetRepository.receipt = _receipt()

    result = await verify_adoption_bundle(
        engine,  # type: ignore[arg-type]
        bundle=_bundle(),
        repository_factory=_TargetRepository,  # type: ignore[arg-type]
    )

    assert result.dry_run is False
    assert _TargetRepository.events == ["read_receipt", "preflight_baseline"]
    assert engine.conn.transaction.committed is True
    assert "REPEATABLE READ, READ ONLY" in engine.conn.statements[0]


@pytest.mark.asyncio
async def test_exact_receipt_makes_import_idempotent_without_replaying_projection() -> None:
    engine = _Engine(export=False)
    _TargetRepository.events = []
    _TargetRepository.receipt = _receipt()
    _TargetRepository.projection = _sections(offer_name="legitimate later operator change")

    result = await apply_adoption_bundle(
        engine,  # type: ignore[arg-type]
        bundle=_bundle(),
        dry_run=False,
        confirmed_source_fingerprint="a" * 64,
        repository_factory=_TargetRepository,  # type: ignore[arg-type]
    )

    assert result.receipt_created is False
    assert _TargetRepository.events == ["read_receipt", "preflight_baseline"]
    assert engine.conn.transaction.committed is True


@pytest.mark.asyncio
async def test_conflicting_receipt_is_rejected_before_any_import_write() -> None:
    engine = _Engine(export=False)
    _TargetRepository.events = []
    _TargetRepository.receipt = {**_receipt(), "bundle_sha256": "b" * 64}

    with pytest.raises(AdoptionReceiptConflictError, match="different bundle"):
        await apply_adoption_bundle(
            engine,  # type: ignore[arg-type]
            bundle=_bundle(),
            dry_run=False,
            confirmed_source_fingerprint="a" * 64,
            repository_factory=_TargetRepository,  # type: ignore[arg-type]
        )

    assert _TargetRepository.events == ["read_receipt", "preflight_baseline"]
    assert engine.conn.transaction.rolled_back is True


@pytest.mark.asyncio
async def test_first_release_dry_runs_before_import_and_uses_receipt_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    expected = AdoptionImportResult(
        dry_run=False,
        source_fingerprint="a" * 64,
        entity_counts=dict(_bundle().entity_counts),
        section_sha256=dict(_bundle().section_sha256),
    )

    async def fake_apply(*_args, dry_run: bool, **_kwargs):
        calls.append("dry-run" if dry_run else "import")
        return adoption_service.AdoptionImportResult(
            dry_run=dry_run,
            source_fingerprint=expected.source_fingerprint,
            entity_counts=expected.entity_counts,
            section_sha256=expected.section_sha256,
            receipt_created=not dry_run,
        )

    monkeypatch.setattr(adoption_service, "apply_adoption_bundle", fake_apply)

    imported = await adopt_first_release_bundle(  # type: ignore[arg-type]
        object(),
        bundle=_bundle(),
    )
    assert imported.imported is True
    assert calls == ["dry-run", "import"]

    calls.clear()

    async def fake_apply_existing(*_args, dry_run: bool, **_kwargs):
        calls.append("dry-run" if dry_run else "import")
        return adoption_service.AdoptionImportResult(
            dry_run=dry_run,
            source_fingerprint=expected.source_fingerprint,
            entity_counts=expected.entity_counts,
            section_sha256=expected.section_sha256,
            receipt_created=False,
        )

    monkeypatch.setattr(adoption_service, "apply_adoption_bundle", fake_apply_existing)
    verified = await adopt_first_release_bundle(  # type: ignore[arg-type]
        object(),
        bundle=_bundle(),
    )
    assert verified.imported is False
    assert calls == ["dry-run", "import"]
