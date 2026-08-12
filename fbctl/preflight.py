"""Read-only host identity preflight and its narrow SSH transport."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import BinaryIO

from fbctl.bundle import PREFLIGHT_BUNDLE_SCHEMA, inspect_bundle
from fbctl.config import (
    BOOTSTRAP_CADDY_KEYS,
    canonicalize_source,
    parse_bootstrap_source_stdin,
    project_bootstrap_source,
    validate_bootstrap_source_check,
)
from fbctl.errors import FbctlError
from fbctl.files import (
    MAX_DOTENV_BYTES,
    parse_dotenv_payload,
    require_remote,
    snapshot_private_file,
)
from fbctl.identity import resolve_bootstrap_identity, snapshot_host_identity
from fbctl.runner import CommandRunner, SubprocessRunner
from fbctl.vision_profile import validate_production_vision_profile

PRODUCTION_ROOT = Path("/opt/fb-agent")
PRODUCTION_ADOPTION = PRODUCTION_ROOT / "shared" / "adoption-bundle-v1.json"
PRODUCTION_CADDY_ENV = Path("/etc/fb-agent/caddy.env")


def run_host_preflight(source_payload: bytes) -> dict[str, object]:
    if os.geteuid() != 0:
        raise FbctlError("bootstrap identity preflight requires root privileges")
    if sys.version_info < (3, 12):
        raise FbctlError("Python 3.12 or newer is required")
    # Reject an unavailable or unsafe Vision profile before source/caddy
    # handling can allow expensive bootstrap jobs to start.
    validate_production_vision_profile()
    explicit, dropped = project_bootstrap_source(
        parse_bootstrap_source_stdin(source_payload),
        project_known_legacy_source=True,
    )
    host = snapshot_host_identity(PRODUCTION_ROOT, PRODUCTION_ADOPTION, required_uid=0)
    resolved = resolve_bootstrap_identity(
        explicit=explicit,
        canonical=host.canonical_values,
        legacy=host.legacy_values,
        adoption_owner=(host.adoption.owner_telegram_user_id if host.adoption else None),
        migration_enabled=True,
    )
    merged = dict(explicit)
    merged.update(resolved.values)
    if not any(key in merged for key in BOOTSTRAP_CADDY_KEYS):
        caddy = snapshot_private_file(
            PRODUCTION_CADDY_ENV,
            label="existing Caddy credentials",
            maximum=MAX_DOTENV_BYTES,
            required_uid=0,
        )
        assert caddy is not None
        caddy_values = parse_dotenv_payload(
            caddy.payload,
            label="existing Caddy credentials",
            maximum=MAX_DOTENV_BYTES,
        )
        for key in BOOTSTRAP_CADDY_KEYS:
            merged[key] = caddy_values.get(key, "")
    validate_bootstrap_source_check(merged)
    canonicalize_source(merged, incumbent=host.canonical_values)
    return {
        "status": "READY",
        "dropped_keys": list(dropped),
        "oidc_origin": resolved.oidc_origin,
        "owner_origin": resolved.owner_origin,
    }


def bootstrap_remote_preflight(
    *,
    host: str,
    bundle: Path,
    source_env_stdin: bool,
    project_known_legacy_source: bool,
    migrate_existing_bootstrap_identity: bool,
    runner: CommandRunner | None = None,
    source_stream: BinaryIO | None = None,
) -> dict[str, object]:
    runner = runner or SubprocessRunner()
    host = require_remote(host)
    metadata = inspect_bundle(bundle)
    if metadata.schema != PREFLIGHT_BUNDLE_SCHEMA:
        raise FbctlError("bootstrap remote preflight requires a preflight bundle")
    if not (
        source_env_stdin and project_known_legacy_source and migrate_existing_bootstrap_identity
    ):
        raise FbctlError("bootstrap remote preflight requires its explicit migration contract")
    payload = (source_stream or sys.stdin.buffer).read(2_000_001)
    if not payload or len(payload) > 2_000_000 or b"\x00" in payload:
        raise FbctlError("source environment stdin is empty or exceeds 2 MB")
    try:
        source_text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FbctlError("source environment stdin is not valid UTF-8") from exc

    upload_stage: Path | None = None
    upload_bundle: Path | None = None
    root_stage: Path | None = None
    root_bundle: Path | None = None
    try:
        runner.run(("ssh", host, "sudo", "-n", "true"), step="bootstrap_remote_preflight")
        staged = runner.run(
            ("ssh", host, "mktemp", "-d", f"/tmp/fbctl-{metadata.release_id}-XXXXXXXX"),
            step="bootstrap_remote_preflight",
            capture=True,
        )
        upload_stage = _validated_remote_stage(staged.stdout, metadata.release_id)
        upload_bundle = upload_stage / "preflight.pyz"
        runner.run(("scp", bundle, f"{host}:{upload_bundle}"), step="bootstrap_remote_preflight")
        root_staged = runner.run(
            (
                "ssh",
                host,
                "sudo",
                "-n",
                "mktemp",
                "-d",
                f"/tmp/fbctl-root-{metadata.release_id}-XXXXXXXX",
            ),
            step="bootstrap_remote_preflight",
            capture=True,
        )
        root_stage = _validated_remote_stage(
            root_staged.stdout,
            metadata.release_id,
            root_owned=True,
        )
        root_bundle = root_stage / "preflight.pyz"
        runner.run(
            (
                "ssh",
                host,
                "sudo",
                "-n",
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0500",
                "--",
                upload_bundle,
                root_bundle,
            ),
            step="bootstrap_remote_preflight",
        )
        digest = runner.run(
            ("ssh", host, "sudo", "-n", "sha256sum", "--", root_bundle),
            step="bootstrap_remote_preflight",
            capture=True,
        )
        _verify_remote_bundle_digest(digest.stdout, root_bundle, metadata.sha256)
        runner.run(
            (
                "ssh",
                host,
                "sudo",
                "-n",
                "python3",
                "-B",
                root_bundle,
                "--stdin",
                "--project-known-legacy-source",
                "--migrate-existing-bootstrap-identity",
            ),
            step="bootstrap_remote_preflight",
            input_text=source_text,
        )
    finally:
        if root_stage is not None and root_bundle is not None:
            runner.run(
                ("ssh", host, "sudo", "-n", "rm", "-f", "--", root_bundle),
                step="bootstrap_remote_preflight_cleanup",
                check=False,
            )
            runner.run(
                ("ssh", host, "sudo", "-n", "rmdir", "--", root_stage),
                step="bootstrap_remote_preflight_cleanup",
                check=False,
            )
        if upload_stage is not None and upload_bundle is not None:
            runner.run(
                ("ssh", host, "rm", "-f", "--", upload_bundle),
                step="bootstrap_remote_preflight_cleanup",
                check=False,
            )
            runner.run(
                ("ssh", host, "rmdir", "--", upload_stage),
                step="bootstrap_remote_preflight_cleanup",
                check=False,
            )
    return {"status": "READY", "release_id": metadata.release_id}


def _validated_remote_stage(
    raw: str,
    release_id: str,
    *,
    root_owned: bool = False,
) -> Path:
    rendered = raw.strip()
    prefix = f"fbctl-{'root-' if root_owned else ''}{release_id}-"
    path = Path(rendered)
    suffix = path.name.removeprefix(prefix)
    if (
        "\n" in rendered
        or "\r" in rendered
        or path.parent != Path("/tmp")
        or not path.name.startswith(prefix)
        or len(suffix) < 6
        or not suffix.isalnum()
    ):
        raise FbctlError("remote mktemp returned an invalid staging path")
    return path


def _verify_remote_bundle_digest(raw: str, path: Path, expected: str) -> None:
    fields = raw.strip().split()
    if len(fields) != 2 or fields[0] != expected or fields[1] != os.fspath(path):
        raise FbctlError("remote control bundle integrity check failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fbctl-preflight")
    parser.add_argument("--stdin", action="store_true", required=True)
    parser.add_argument("--project-known-legacy-source", action="store_true", required=True)
    parser.add_argument("--migrate-existing-bootstrap-identity", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        build_parser().parse_args(argv)
        result = run_host_preflight(sys.stdin.buffer.read(2_000_001))
    except FbctlError as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return exc.exit_code
    except Exception:  # noqa: BLE001 - remote boundary must never emit traceback or secrets
        print(
            json.dumps({"status": "FAILED", "error": "unexpected preflight failure"}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0
