from __future__ import annotations

import base64
import hashlib
import io
import os
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from fbctl import preflight as fbctl_preflight
from fbctl.bundle import (
    PREFLIGHT_BUNDLE_SCHEMA,
    PREFLIGHT_MODULES,
    build_preflight_bundle,
    inspect_bundle,
)
from fbctl.errors import FbctlError
from fbctl.preflight import bootstrap_remote_preflight
from fbctl.runner import CommandResult

ROOT = Path(__file__).resolve().parents[2]


def test_host_preflight_validates_existing_caddy_fallback_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fbctl_preflight, "validate_production_vision_profile", lambda: None)
    source = (
        b"ENCRYPTION_KEY="
        + base64.urlsafe_b64encode(b"e" * 32)
        + b"\nENCRYPTION_KEY_VERIFY=verify\n"
        + b"TELEGRAM_BOT_TOKEN=333333:test\nAPI_KEY="
        + b"a" * 32
        + b"\nVISION_X_TOKEN=token\nVISION_PROFILE_ID=profile\n"
    )
    identity_values = {
        "TELEGRAM_OIDC_CLIENT_ID": "111111",
        "TELEGRAM_OIDC_CLIENT_SECRET": "s" * 40,
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "222222",
    }
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        fbctl_preflight,
        "snapshot_host_identity",
        lambda *_args, **_kwargs: SimpleNamespace(
            canonical_values={}, legacy_values=identity_values, adoption=None
        ),
    )
    monkeypatch.setattr(
        fbctl_preflight,
        "snapshot_private_file",
        lambda *_args, **_kwargs: SimpleNamespace(
            payload=(
                b"PANEL_BASIC_AUTH_USER=owner\n"
                + b"PANEL_BASIC_AUTH_HASH=$2b$12$"
                + b"a" * 53
                + b"\n"
            )
        ),
    )

    result = fbctl_preflight.run_host_preflight(source)

    assert result["status"] == "READY"
    assert result["oidc_origin"] == "legacy"
    assert result["owner_origin"] == "legacy"


def test_host_preflight_rejects_missing_caddy_fallback_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fbctl_preflight, "validate_production_vision_profile", lambda: None)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        fbctl_preflight,
        "snapshot_host_identity",
        lambda *_args, **_kwargs: SimpleNamespace(
            canonical_values={},
            legacy_values={
                "TELEGRAM_OIDC_CLIENT_ID": "111111",
                "TELEGRAM_OIDC_CLIENT_SECRET": "s" * 40,
                "DESKTOP_OWNER_TELEGRAM_USER_ID": "222222",
            },
            adoption=None,
        ),
    )
    monkeypatch.setattr(
        fbctl_preflight,
        "snapshot_private_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FbctlError("missing Caddy fallback")),
    )

    with pytest.raises(FbctlError, match="missing Caddy fallback"):
        fbctl_preflight.run_host_preflight(b"VISION_X_TOKEN=token\nVISION_PROFILE_ID=profile\n")


def test_host_preflight_rejects_old_python_before_reading_host_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(fbctl_preflight.sys, "version_info", (3, 11, 9))
    monkeypatch.setattr(
        fbctl_preflight,
        "snapshot_host_identity",
        lambda *_args, **_kwargs: pytest.fail("host files must not be read"),
    )

    with pytest.raises(FbctlError, match="Python 3.12"):
        fbctl_preflight.run_host_preflight(b"API_KEY=secret\n")


def test_host_preflight_rejects_profile_before_identity_or_caddy_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        fbctl_preflight,
        "validate_production_vision_profile",
        lambda: (_ for _ in ()).throw(FbctlError("desktop profile seed marker is invalid")),
    )
    monkeypatch.setattr(
        fbctl_preflight,
        "snapshot_host_identity",
        lambda *_args, **_kwargs: pytest.fail("identity must not be read"),
    )
    monkeypatch.setattr(
        fbctl_preflight,
        "snapshot_private_file",
        lambda *_args, **_kwargs: pytest.fail("Caddy credentials must not be read"),
    )

    with pytest.raises(FbctlError, match="desktop profile seed marker is invalid"):
        fbctl_preflight.run_host_preflight(b"API_KEY=secret\n")


class RecordingRunner:
    def __init__(self, *, fail_execution: bool = False) -> None:
        self.calls: list[tuple[tuple[str, ...], str, str | None]] = []
        self.fail_execution = fail_execution
        self.uploaded_bundle: Path | None = None

    def run(
        self,
        command,
        *,
        step,
        env=None,
        capture=False,
        check=True,
        input_text=None,
        timeout=None,
    ) -> CommandResult:
        del env, capture, check, timeout
        argv = tuple(os.fspath(part) for part in command)
        self.calls.append((argv, step, input_text))
        if argv and argv[0] == "scp":
            self.uploaded_bundle = Path(argv[1])
        if "mktemp" in argv:
            return CommandResult(0, f"{argv[-1].replace('XXXXXXXX', 'AbCd1234')}\n")
        if "sha256sum" in argv:
            assert self.uploaded_bundle is not None
            digest = hashlib.sha256(self.uploaded_bundle.read_bytes()).hexdigest()
            return CommandResult(0, f"{digest}  {argv[-1]}\n")
        if self.fail_execution and "python3" in argv:
            raise FbctlError("remote preflight failed")
        return CommandResult(0)


def test_preflight_bundle_is_deterministic_runnable_and_exactly_allowlisted(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.pyz"
    second = tmp_path / "second.pyz"
    build_preflight_bundle(source_root=ROOT, output=first, release_id="release-1")
    build_preflight_bundle(source_root=ROOT, output=second, release_id="release-1")

    assert first.read_bytes() == second.read_bytes()
    metadata = inspect_bundle(first)
    assert metadata.schema == PREFLIGHT_BUNDLE_SCHEMA
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == {
            "__main__.py",
            "fbctl/resources/artifact-manifest.json",
            *(f"fbctl/{name}" for name in PREFLIGHT_MODULES),
        }
        assert "fbctl/controller.py" not in archive.namelist()
        assert "fbctl/publish.py" not in archive.namelist()
        assert "fbctl/resources/release.json" not in archive.namelist()
    completed = subprocess.run(
        ["python3", "-B", os.fspath(first), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "migrate-existing-bootstrap-identity" in completed.stdout


def test_remote_preflight_streams_source_only_to_root_owned_bundle_stdin(tmp_path: Path) -> None:
    bundle = tmp_path / "preflight.pyz"
    build_preflight_bundle(source_root=ROOT, output=bundle, release_id="release-1")
    source = b"API_KEY=never-render-this-secret\n"
    runner = RecordingRunner()

    result = bootstrap_remote_preflight(
        host="deploy@example.test",
        bundle=bundle,
        source_env_stdin=True,
        project_known_legacy_source=True,
        migrate_existing_bootstrap_identity=True,
        runner=runner,
        source_stream=io.BytesIO(source),
    )

    assert result == {"status": "READY", "release_id": "release-1"}
    rendered = "\n".join(" ".join(call[0]) for call in runner.calls)
    assert "never-render-this-secret" not in rendered
    assert "source.env" not in rendered
    scp_calls = [call[0] for call in runner.calls if call[0][0] == "scp"]
    assert len(scp_calls) == 1
    assert scp_calls[0][2].endswith("/preflight.pyz")
    execution = next(call for call in runner.calls if "python3" in call[0])
    assert execution[2] == source.decode("utf-8")
    install_index = next(index for index, call in enumerate(runner.calls) if "install" in call[0])
    install = runner.calls[install_index][0]
    assert "/tmp/fbctl-release-1-AbCd1234/preflight.pyz" in install
    assert "/tmp/fbctl-root-release-1-AbCd1234/preflight.pyz" in install
    digest_index = next(index for index, call in enumerate(runner.calls) if "sha256sum" in call[0])
    execute_index = runner.calls.index(execution)
    assert install_index < digest_index < execute_index
    cleanup = [call for call in runner.calls if call[1] == "bootstrap_remote_preflight_cleanup"]
    assert any("sudo" in call[0] and "rmdir" in call[0] for call in cleanup)


def test_remote_preflight_cleans_root_owned_stage_after_failure(tmp_path: Path) -> None:
    bundle = tmp_path / "preflight.pyz"
    build_preflight_bundle(source_root=ROOT, output=bundle, release_id="release-1")
    runner = RecordingRunner(fail_execution=True)

    with pytest.raises(FbctlError, match="remote preflight failed"):
        bootstrap_remote_preflight(
            host="deploy@example.test",
            bundle=bundle,
            source_env_stdin=True,
            project_known_legacy_source=True,
            migrate_existing_bootstrap_identity=True,
            runner=runner,
            source_stream=io.BytesIO(b"API_KEY=secret\n"),
        )

    assert [call[1] for call in runner.calls].count("bootstrap_remote_preflight_cleanup") == 4


def test_remote_preflight_rejects_root_copy_digest_mismatch_before_execution(
    tmp_path: Path,
) -> None:
    class BadDigestRunner(RecordingRunner):
        def run(self, command, **kwargs):
            result = super().run(command, **kwargs)
            argv = tuple(os.fspath(part) for part in command)
            if "sha256sum" in argv:
                return CommandResult(0, f"{'0' * 64}  {argv[-1]}\n")
            return result

    bundle = tmp_path / "preflight.pyz"
    build_preflight_bundle(source_root=ROOT, output=bundle, release_id="release-1")
    runner = BadDigestRunner()

    with pytest.raises(FbctlError, match="integrity check failed"):
        bootstrap_remote_preflight(
            host="deploy@example.test",
            bundle=bundle,
            source_env_stdin=True,
            project_known_legacy_source=True,
            migrate_existing_bootstrap_identity=True,
            runner=runner,
            source_stream=io.BytesIO(b"API_KEY=secret\n"),
        )

    assert not any("python3" in call[0] for call in runner.calls)
    assert [call[1] for call in runner.calls].count("bootstrap_remote_preflight_cleanup") == 4


@pytest.mark.parametrize(
    "source_stdin,legacy_projection,migration",
    [(False, True, True), (True, False, True), (True, True, False)],
)
def test_remote_preflight_rejects_incomplete_explicit_contract_before_remote_calls(
    tmp_path: Path,
    source_stdin: bool,
    legacy_projection: bool,
    migration: bool,
) -> None:
    bundle = tmp_path / "preflight.pyz"
    build_preflight_bundle(source_root=ROOT, output=bundle, release_id="release-1")
    runner = RecordingRunner()
    with pytest.raises(FbctlError, match="explicit migration contract"):
        bootstrap_remote_preflight(
            host="deploy@example.test",
            bundle=bundle,
            source_env_stdin=source_stdin,
            project_known_legacy_source=legacy_projection,
            migrate_existing_bootstrap_identity=migration,
            runner=runner,
            source_stream=io.BytesIO(b"API_KEY=secret\n"),
        )
    assert runner.calls == []
