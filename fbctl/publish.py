"""Minimal SSH transport for one verified fbctl zipapp release."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO

from fbctl.bundle import inspect_bundle
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
    runner: CommandRunner | None = None,
    source_stream: BinaryIO | None = None,
) -> dict[str, object]:
    runner = runner or SubprocessRunner()
    host = require_remote(host)
    root = _validate_remote_path(root, label="root", scope="root")
    metadata = inspect_bundle(bundle)
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
    source_payload: bytes | None = None
    if bootstrap:
        source_payload = (source_stream or sys.stdin.buffer).read(2_000_001)
        if not source_payload or len(source_payload) > 2_000_000 or b"\x00" in source_payload:
            raise FbctlError("source environment stdin is empty or exceeds 2 MB")
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
    remote_stage: Path | None = None
    remote_bundle: Path | None = None
    remote_source: Path | None = None
    local_source: Path | None = None
    try:
        runner.run(("ssh", host, "sudo", "-n", "true"), step="publish")
        if source_payload is not None:
            descriptor, local_source_name = tempfile.mkstemp(prefix=".fbctl-source-")
            local_source = Path(local_source_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(source_payload)
                handle.flush()
                os.fsync(handle.fileno())
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
        remote_stage = _validated_remote_stage(staged.stdout, metadata.release_id)
        remote_bundle = remote_stage / "release.pyz"
        remote_source = remote_stage / "source.env" if bootstrap else None
        runner.run(("scp", bundle, f"{host}:{remote_bundle}"), step="publish")
        if local_source is not None and remote_source is not None:
            runner.run(("scp", local_source, f"{host}:{remote_source}"), step="publish")
        runner.run(
            ("ssh", host, "chmod", "0500", remote_bundle),
            step="publish",
        )
        if remote_source is not None:
            runner.run(
                ("ssh", host, "chmod", "0600", remote_source),
                step="publish",
            )
        if bootstrap:
            assert remote_source is not None
            command: list[str | Path] = [
                "ssh",
                host,
                "sudo",
                "-n",
                "python3",
                "-B",
                remote_bundle,
                "bootstrap",
                "--root",
                root,
                "--source-env",
                remote_source,
            ]
            if adoption_bundle_remote is not None:
                command.extend(("--adoption-bundle", adoption_bundle_remote))
            if desktop_profile_seed_remote is not None:
                command.extend(("--desktop-profile-seed", desktop_profile_seed_remote))
            if docker_config is not None:
                command.extend(("--docker-config", docker_config))
            if reuse_existing_caddy_credentials:
                command.append("--reuse-existing-caddy-credentials")
            runner.run(command, step="publish")
        deploy: list[str | Path] = [
            "ssh",
            host,
            "sudo",
            "-n",
            "python3",
            "-B",
            remote_bundle,
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
        if local_source is not None:
            local_source.unlink(missing_ok=True)
        if remote_stage is not None and remote_bundle is not None:
            staged_files = (
                (remote_bundle,) if remote_source is None else (remote_source, remote_bundle)
            )
            runner.run(
                ("ssh", host, "rm", "-f", "--", *staged_files),
                step="publish_cleanup",
                check=False,
            )
            runner.run(
                ("ssh", host, "rmdir", "--", remote_stage),
                step="publish_cleanup",
                check=False,
            )
    return {
        "status": "READY",
        "release_id": metadata.release_id,
        "bundle_sha256": metadata.sha256,
    }


def _validated_remote_stage(raw: str, release_id: str) -> Path:
    rendered = raw.strip()
    if "\n" in rendered or "\r" in rendered:
        raise FbctlError("remote mktemp returned an invalid staging path")
    path = Path(rendered)
    expected_prefix = f"fbctl-{release_id}-"
    if path.parent != Path("/tmp") or not path.name.startswith(expected_prefix):
        raise FbctlError("remote mktemp returned a path outside the fbctl staging scope")
    suffix = path.name.removeprefix(expected_prefix)
    if len(suffix) < 6 or not suffix.isalnum():
        raise FbctlError("remote mktemp returned an invalid staging suffix")
    return path


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
