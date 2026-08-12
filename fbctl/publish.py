"""Minimal SSH transport for one verified fbctl zipapp release."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import BinaryIO

from fbctl.bundle import BUNDLE_SCHEMA, inspect_bundle
from fbctl.errors import FbctlError
from fbctl.files import require_remote
from fbctl.runner import CommandRunner, SubprocessRunner


def publish(
    *,
    host: str,
    bundle: Path,
    root: Path,
    source_env_stdin: bool,
    docker_config: Path | None,
    bootstrap: bool,
    adoption_bundle_remote: Path | None,
    desktop_profile_seed_remote: Path | None,
    enable_scanning: bool,
    reuse_existing_caddy_credentials: bool = False,
    project_known_legacy_source: bool = False,
    migrate_existing_bootstrap_identity: bool = False,
    runner: CommandRunner | None = None,
    source_stream: BinaryIO | None = None,
) -> dict[str, object]:
    runner = runner or SubprocessRunner()
    host = require_remote(host)
    root = _validate_remote_path(root, label="root", scope="root")
    metadata = inspect_bundle(bundle)
    if metadata.schema != BUNDLE_SCHEMA:
        raise FbctlError("publish requires a full release bundle")
    if bootstrap != source_env_stdin:
        raise FbctlError("source environment stdin is accepted only for explicit bootstrap")
    if not bootstrap and (
        adoption_bundle_remote is not None
        or desktop_profile_seed_remote is not None
        or enable_scanning
    ):
        raise FbctlError("bootstrap-only publish options require --bootstrap")
    if reuse_existing_caddy_credentials and not bootstrap:
        raise FbctlError("Caddy credential reuse requires --bootstrap")
    if project_known_legacy_source and not bootstrap:
        raise FbctlError("legacy source projection requires --bootstrap")
    if migrate_existing_bootstrap_identity and not bootstrap:
        raise FbctlError("bootstrap identity migration requires --bootstrap")
    source_payload: bytes | None = None
    source_text: str | None = None
    if bootstrap:
        source_payload = (source_stream or sys.stdin.buffer).read(2_000_001)
        if not source_payload or len(source_payload) > 2_000_000 or b"\x00" in source_payload:
            raise FbctlError("source environment stdin is empty or exceeds 2 MB")
        try:
            source_text = source_payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FbctlError("source environment stdin is not valid UTF-8") from exc
    if docker_config is not None:
        docker_config = _validate_remote_path(
            docker_config,
            label="remote Docker config",
            scope="docker_config",
        )
    if adoption_bundle_remote is not None:
        adoption_bundle_remote = _validate_remote_path(
            adoption_bundle_remote,
            label="adoption bundle",
            scope="shared_input",
        )
    if desktop_profile_seed_remote is not None:
        desktop_profile_seed_remote = _validate_remote_path(
            desktop_profile_seed_remote,
            label="desktop profile seed",
            scope="shared_input",
        )
    upload_stage: Path | None = None
    upload_bundle: Path | None = None
    root_stage: Path | None = None
    root_bundle: Path | None = None
    try:
        runner.run(("ssh", host, "sudo", "-n", "true"), step="publish")
        staged = runner.run(
            (
                "ssh",
                host,
                "mktemp",
                "-d",
                f"/tmp/fbctl-{metadata.release_id}-XXXXXXXX",
            ),
            step="publish",
            capture=True,
        )
        upload_stage = _validated_remote_stage(staged.stdout, metadata.release_id)
        upload_bundle = upload_stage / "release.pyz"
        runner.run(("scp", bundle, f"{host}:{upload_bundle}"), step="publish")
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
            step="publish",
            capture=True,
        )
        root_stage = _validated_remote_stage(
            root_staged.stdout,
            metadata.release_id,
            root_owned=True,
        )
        root_bundle = root_stage / "release.pyz"
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
            step="publish",
        )
        digest = runner.run(
            ("ssh", host, "sudo", "-n", "sha256sum", "--", root_bundle),
            step="publish",
            capture=True,
        )
        _verify_remote_bundle_digest(digest.stdout, root_bundle, metadata.sha256)
        if bootstrap:
            assert source_payload is not None
            assert source_text is not None
            command: list[str | Path] = [
                "ssh",
                host,
                "sudo",
                "-n",
                "python3",
                "-B",
                root_bundle,
                "bootstrap",
                "--root",
                root,
                "--source-env-stdin",
            ]
            if adoption_bundle_remote is not None:
                command.extend(("--adoption-bundle", adoption_bundle_remote))
            if desktop_profile_seed_remote is not None:
                command.extend(("--desktop-profile-seed", desktop_profile_seed_remote))
            if docker_config is not None:
                command.extend(("--docker-config", docker_config))
            if reuse_existing_caddy_credentials:
                command.append("--reuse-existing-caddy-credentials")
            if project_known_legacy_source:
                command.append("--project-known-legacy-source")
            if migrate_existing_bootstrap_identity:
                command.append("--migrate-existing-bootstrap-identity")
            runner.run(command, step="publish", input_text=source_text)
        deploy: list[str | Path] = [
            "ssh",
            host,
            "sudo",
            "-n",
            "python3",
            "-B",
            root_bundle,
            "deploy",
            "--root",
            root,
        ]
        if docker_config is not None:
            deploy.extend(("--docker-config", docker_config))
        if enable_scanning:
            deploy.append("--enable-scanning")
        runner.run(deploy, step="publish")
    finally:
        if root_stage is not None and root_bundle is not None:
            runner.run(
                ("ssh", host, "sudo", "-n", "rm", "-f", "--", root_bundle),
                step="publish_cleanup",
                check=False,
            )
            runner.run(
                ("ssh", host, "sudo", "-n", "rmdir", "--", root_stage),
                step="publish_cleanup",
                check=False,
            )
        if upload_stage is not None and upload_bundle is not None:
            runner.run(
                ("ssh", host, "rm", "-f", "--", upload_bundle),
                step="publish_cleanup",
                check=False,
            )
            runner.run(
                ("ssh", host, "rmdir", "--", upload_stage),
                step="publish_cleanup",
                check=False,
            )
    return {
        "status": "READY",
        "release_id": metadata.release_id,
        "bundle_sha256": metadata.sha256,
    }


def _validated_remote_stage(
    raw: str,
    release_id: str,
    *,
    root_owned: bool = False,
) -> Path:
    rendered = raw.strip()
    if "\n" in rendered or "\r" in rendered:
        raise FbctlError("remote mktemp returned an invalid staging path")
    path = Path(rendered)
    expected_prefix = f"fbctl-{'root-' if root_owned else ''}{release_id}-"
    if path.parent != Path("/tmp") or not path.name.startswith(expected_prefix):
        raise FbctlError("remote mktemp returned a path outside the fbctl staging scope")
    suffix = path.name.removeprefix(expected_prefix)
    if len(suffix) < 6 or not suffix.isalnum():
        raise FbctlError("remote mktemp returned an invalid staging suffix")
    return path


def _verify_remote_bundle_digest(raw: str, path: Path, expected: str) -> None:
    fields = raw.strip().split()
    if len(fields) != 2 or fields[0] != expected or fields[1] != os.fspath(path):
        raise FbctlError("remote control bundle integrity check failed")


_REMOTE_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")
_DOCKER_CONFIG = re.compile(r"^/tmp/fb-agent-ghcr-[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHARED_BOOTSTRAP_INPUTS = frozenset(
    {
        "/opt/fb-agent/shared/adoption-bundle-v1.json",
        "/opt/fb-agent/shared/vision-profile-seed",
    }
)


def _validate_remote_path(path: Path, *, label: str, scope: str) -> Path:
    """Accept only lexically safe paths in the explicitly supported SSH scopes.

    OpenSSH executes the remote command through a shell even when argv is used
    locally.  Paths therefore must be validated before *any* runner invocation,
    not quoted after the fact.
    """

    raw = os.fspath(path)
    if (
        not _REMOTE_PATH.fullmatch(raw)
        or raw == "/"
        or any(part in {"", ".", ".."} for part in raw.split("/")[1:])
    ):
        raise FbctlError(f"{label} must be a safe canonical remote path")
    if scope == "root":
        valid = raw == "/opt/fb-agent"
    elif scope == "docker_config":
        valid = bool(_DOCKER_CONFIG.fullmatch(raw))
    elif scope == "shared_input":
        valid = raw in _SHARED_BOOTSTRAP_INPUTS
    else:  # pragma: no cover - internal invariant
        raise FbctlError(f"unknown remote path scope: {scope}")
    if not valid:
        raise FbctlError(f"{label} is outside its permitted remote scope")
    return Path(raw)
