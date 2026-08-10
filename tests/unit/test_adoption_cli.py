from __future__ import annotations

import json
import stat
from datetime import UTC, datetime

import pytest

from apps.cleanup_worker.retention import get_default_policy
from core.adoption import cli
from core.adoption.bundle import (
    AdoptionAccountV1,
    AdoptionOfferV1,
    AdoptionSectionsV1,
    AdoptionSystemSettingsV1,
    build_adoption_bundle,
    canonical_bundle_json,
)
from core.adoption.cli import AdoptionCliError, write_private_bundle
from core.adoption.service import AdoptionImportResult


def _bundle():
    sections = AdoptionSectionsV1(
        accounts=[AdoptionAccountV1(account_id="111")],
        offers=[
            AdoptionOfferV1(
                code="GH_CR2",
                name="Ghana",
                is_active=True,
                account_ids=["111"],
            )
        ],
        system_settings=AdoptionSystemSettingsV1(
            retention_policy=get_default_policy(),
            web_app_url=None,
        ),
    )
    return build_adoption_bundle(
        sections,
        exported_at=datetime(2026, 8, 9, tzinfo=UTC),
        source_fingerprint="a" * 64,
    )


def test_output_is_mode_0600_and_refuses_overwrite(tmp_path) -> None:
    destination = tmp_path / "adoption.json"

    write_private_bundle(destination, canonical_bundle_json(_bundle()))

    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    original = destination.read_bytes()
    with pytest.raises(AdoptionCliError, match="already exists"):
        write_private_bundle(destination, "replacement")
    assert destination.read_bytes() == original


def test_output_refuses_existing_symlink(tmp_path) -> None:
    target = tmp_path / "target.json"
    target.write_text("preserve", encoding="utf-8")
    destination = tmp_path / "adoption.json"
    destination.symlink_to(target)

    with pytest.raises(AdoptionCliError, match="already exists"):
        write_private_bundle(destination, canonical_bundle_json(_bundle()))

    assert target.read_text(encoding="utf-8") == "preserve"


def test_validate_reports_only_stable_fingerprint(tmp_path, capsys) -> None:
    source = tmp_path / "adoption.json"
    source.write_text(canonical_bundle_json(_bundle()), encoding="utf-8")

    assert cli.main(["validate", "--input", str(source)]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == f"bundle valid; source_fingerprint={'a' * 64}\n"


def test_tamper_failure_never_prints_payload_or_dsn(tmp_path, capsys, monkeypatch) -> None:
    source = tmp_path / "adoption.json"
    raw = json.loads(canonical_bundle_json(_bundle()))
    raw["sections"]["offers"][0]["name"] = "postgresql://admin:secret@db/prod"
    source.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv(
        cli.TARGET_DSN_ENV,
        "postgresql+asyncpg://admin:another-secret@db.example/prod",
    )
    monkeypatch.setattr(
        cli,
        "_engine",
        lambda _env_name: pytest.fail("invalid bundle must fail before database access"),
    )

    assert cli.main(["dry-run", "--input", str(source)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "adoption command failed\n"
    assert "secret" not in captured.err
    assert "db.example" not in captured.err


def test_import_requires_exact_confirmation_before_database_access(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    source = tmp_path / "adoption.json"
    source.write_text(canonical_bundle_json(_bundle()), encoding="utf-8")

    def forbidden_engine(_env_name: str):
        raise AssertionError("database must not be opened")

    monkeypatch.setattr(cli, "_engine", forbidden_engine)

    assert (
        cli.main(
            [
                "import",
                "--input",
                str(source),
                "--source-fingerprint",
                "a" * 64,
                "--confirm",
                "yes",
            ]
        )
        == 1
    )
    assert capsys.readouterr().err == "adoption command failed\n"


class _Engine:
    disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_real_import_forwards_exact_fingerprint_and_disposes_engine(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "adoption.json"
    source.write_text(canonical_bundle_json(_bundle()), encoding="utf-8")
    engine = _Engine()
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_engine", lambda _env_name: engine)

    async def fake_apply(_engine, **kwargs):
        captured.update(kwargs)
        bundle = kwargs["bundle"]
        return AdoptionImportResult(
            dry_run=kwargs["dry_run"],
            source_fingerprint=bundle.source_fingerprint,
            entity_counts=dict(bundle.entity_counts),
            section_sha256=dict(bundle.section_sha256),
        )

    monkeypatch.setattr(cli, "apply_adoption_bundle", fake_apply)
    args = cli._parser().parse_args(
        [
            "import",
            "--input",
            str(source),
            "--source-fingerprint",
            "a" * 64,
            "--confirm",
            cli.IMPORT_CONFIRMATION,
        ]
    )

    message = await cli._run(args)

    assert message == f"import complete; source_fingerprint={'a' * 64}"
    assert captured["dry_run"] is False
    assert captured["confirmed_source_fingerprint"] == "a" * 64
    assert engine.disposed is True


def test_cli_has_no_database_url_argument_or_echo(capsys) -> None:
    assert (
        cli.main(
            [
                "dry-run",
                "--input",
                "bundle.json",
                "--database-url",
                "postgresql://admin:secret@db/prod",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "adoption command failed\n"
    assert "secret" not in captured.err
