#!/usr/bin/env python3
"""Provision durable generated production secrets exactly once."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import secrets
import stat
import tempfile
import uuid
from pathlib import Path

CLUSTER_ID_RE = re.compile(r"^[0-9a-f]{32}$")
KEY_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
INSECURE_POSTGRES_PASSWORDS = frozenset({"", "fb_stop_bot"})
DURABLE_GENERATED_SECRETS = {
    "TELEGRAM_WEBHOOK_SECRET": 32,
    "ALERTMANAGER_WEBHOOK_SECRET": 32,
    "TMA_SESSION_SECRET": 32,
    "ADSETPRO_POSTBACK_SECRET": 32,
    "DESKTOP_RUSTDESK_PASSWORD": 32,
    "BROWSER_MAINTENANCE_CAPABILITY_SECRET": 48,
    "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE": 48,
    "BROWSER_OPERATION_CAPABILITY_SECRET_META_API": 48,
    "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR": 48,
    "BROWSER_AUTHORITY_CONSUMER_TOKEN": 48,
}
FORBIDDEN_SHARED_BROWSER_KEYS = frozenset(
    {
        "BROWSER_MAINTENANCE_CAPABILITY_SECRET",
        "BROWSER_OPERATION_CAPABILITY_SECRET",
        "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE",
        "BROWSER_OPERATION_CAPABILITY_SECRET_META_API",
        "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR",
        "BROWSER_AUTHORITY_CONSUMER_TOKEN",
    }
)
SCOPED_BROWSER_OUTPUT_KEYS = {
    "BROWSER_MAINTENANCE_CAPABILITY_SECRET": "BROWSER_MAINTENANCE_CAPABILITY_SECRET",
    "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE": ("BROWSER_OPERATION_CAPABILITY_SECRET"),
    "BROWSER_OPERATION_CAPABILITY_SECRET_META_API": ("BROWSER_OPERATION_CAPABILITY_SECRET"),
    "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR": ("BROWSER_OPERATION_CAPABILITY_SECRET"),
    "BROWSER_AUTHORITY_CONSUMER_TOKEN": "BROWSER_AUTHORITY_CONSUMER_TOKEN",
}


def parse_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = KEY_RE.match(line.strip())
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_private_file(path: Path, *, owner_uid: int) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{path} must be a regular file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError(f"{path} must have mode 600")
    if metadata.st_uid != owner_uid:
        raise ValueError(f"{path} has an unexpected owner")


def _secure_input_password(values: dict[str, str]) -> str:
    password = values.get("POSTGRES_PASSWORD", "")
    database = values.get("POSTGRES_DB", "fb_stop_bot")
    if password in INSECURE_POSTGRES_PASSWORDS or password == database or len(password) < 16:
        return ""
    return password


def _read_existing(path: Path, *, owner_uid: int) -> dict[str, str]:
    _validate_private_file(path, owner_uid=owner_uid)
    values = parse_values(path.read_text(encoding="utf-8"))
    cluster_id = values.get("FB_AGENT_BOOTSTRAP_CLUSTER_ID", "")
    password = values.get("POSTGRES_PASSWORD", "")
    if not CLUSTER_ID_RE.fullmatch(cluster_id):
        raise ValueError("durable bootstrap cluster id is invalid")
    if len(password) < 16:
        raise ValueError("durable PostgreSQL password is invalid")
    return values


def _render_durable_values(values: dict[str, str]) -> str:
    lines = [
        "# One-time safety-first production secret state",
        f"FB_AGENT_BOOTSTRAP_CLUSTER_ID={values['FB_AGENT_BOOTSTRAP_CLUSTER_ID']}",
        f"POSTGRES_PASSWORD={values['POSTGRES_PASSWORD']}",
    ]
    lines.extend(f"{key}={values[key]}" for key in DURABLE_GENERATED_SECRETS)
    return "\n".join(lines) + "\n"


def _resolved_generated_secrets(
    *,
    source_values: dict[str, str],
    durable_values: dict[str, str],
) -> tuple[dict[str, str], bool]:
    forbidden_shared = sorted(FORBIDDEN_SHARED_BROWSER_KEYS & source_values.keys())
    if forbidden_shared:
        raise ValueError(
            f"{forbidden_shared[0]} must not be stored in the shared source environment"
        )
    resolved = dict(durable_values)
    changed = False
    for key, minimum_length in DURABLE_GENERATED_SECRETS.items():
        source_value = source_values.get(key, "")
        durable_value = durable_values.get(key, "")
        if source_value and len(source_value) < minimum_length:
            raise ValueError(f"shared {key} is too short")
        if durable_value and len(durable_value) < minimum_length:
            raise ValueError(f"durable {key} is invalid")
        if source_value and durable_value and source_value != durable_value:
            raise ValueError(f"shared {key} conflicts with durable secret state")
        if not durable_value:
            resolved[key] = source_value or secrets.token_urlsafe(48)
            changed = True
    return resolved, changed


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_browser_control_env(
    *,
    bootstrap_secrets: Path,
    output: Path,
    owner_uid: int = 0,
) -> None:
    """Render the complete browser-agent verifier keyring and authority token."""
    values = _read_existing(bootstrap_secrets, owner_uid=owner_uid)
    maintenance_secret = values.get("BROWSER_MAINTENANCE_CAPABILITY_SECRET", "")
    operation_secrets = {
        key: values.get(key, "")
        for key in (
            "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE",
            "BROWSER_OPERATION_CAPABILITY_SECRET_META_API",
            "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR",
        )
    }
    authority_token = values.get("BROWSER_AUTHORITY_CONSUMER_TOKEN", "")
    if len(maintenance_secret) < 48:
        raise ValueError("durable browser maintenance capability secret is invalid")
    if any(len(value) < 48 for value in operation_secrets.values()):
        raise ValueError("durable browser operation capability keyring is invalid")
    if len(authority_token) < 48:
        raise ValueError("durable browser authority consumer token is invalid")
    independent_values = [
        maintenance_secret,
        *operation_secrets.values(),
        authority_token,
    ]
    if len(set(independent_values)) != len(independent_values):
        raise ValueError("browser capability secrets must be independently scoped")
    content = "\n".join(
        [
            "# Browser-agent trust-boundary keyring; never log these values",
            f"BROWSER_MAINTENANCE_CAPABILITY_SECRET={maintenance_secret}",
            *(f"{key}={value}" for key, value in operation_secrets.items()),
            f"BROWSER_AUTHORITY_CONSUMER_TOKEN={authority_token}",
            "",
        ]
    )
    if output.exists() or output.is_symlink():
        _validate_private_file(output, owner_uid=owner_uid)
        if output.read_text(encoding="utf-8") == content:
            return
    _atomic_write(output, content)
    _validate_private_file(output, owner_uid=owner_uid)


def write_scoped_browser_env(
    *,
    bootstrap_secrets: Path,
    output: Path,
    capability_key: str,
    output_key: str | None = None,
    owner_uid: int = 0,
) -> None:
    """Render one capability for the exact application service role."""
    expected_output_key = SCOPED_BROWSER_OUTPUT_KEYS.get(capability_key)
    if expected_output_key is None:
        raise ValueError("unsupported browser capability scope")
    rendered_key = output_key or capability_key
    if rendered_key != expected_output_key:
        raise ValueError("browser capability output key does not match its scope")
    values = _read_existing(bootstrap_secrets, owner_uid=owner_uid)
    secret = values.get(capability_key, "")
    if len(secret) < 48:
        raise ValueError(f"durable {capability_key} is invalid")
    content = (
        f"# Least-privilege browser capability; never log this value\n{rendered_key}={secret}\n"
    )
    if output.exists() or output.is_symlink():
        _validate_private_file(output, owner_uid=owner_uid)
        if output.read_text(encoding="utf-8") == content:
            return
    _atomic_write(output, content)
    _validate_private_file(output, owner_uid=owner_uid)


def provision(
    *,
    source: Path,
    output: Path,
    lock_path: Path,
    owner_uid: int = 0,
) -> tuple[str, bool]:
    _validate_private_file(source, owner_uid=owner_uid)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.parent.lstat().st_uid != owner_uid:
        raise ValueError(f"{output.parent} has an unexpected owner")

    lock_flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    lock_descriptor = os.open(lock_path, lock_flags, 0o600)
    try:
        os.fchmod(lock_descriptor, 0o600)
        lock_metadata = os.fstat(lock_descriptor)
        if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_uid != owner_uid:
            raise ValueError("bootstrap secret lock is not a private regular file")
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)

        source_values = parse_values(source.read_text(encoding="utf-8"))
        source_password = _secure_input_password(source_values)
        if output.exists() or output.is_symlink():
            durable_values = _read_existing(
                output,
                owner_uid=owner_uid,
            )
            cluster_id = durable_values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"]
            durable_password = durable_values["POSTGRES_PASSWORD"]
            if source_password and source_password != durable_password:
                raise ValueError(
                    "secure shared POSTGRES_PASSWORD conflicts with durable bootstrap state"
                )
            durable_values, changed = _resolved_generated_secrets(
                source_values=source_values,
                durable_values=durable_values,
            )
            if changed:
                _atomic_write(output, _render_durable_values(durable_values))
                _validate_private_file(output, owner_uid=owner_uid)
            return cluster_id, False

        cluster_id = uuid.uuid4().hex
        durable_password = source_password or secrets.token_urlsafe(48)
        durable_values, _changed = _resolved_generated_secrets(
            source_values=source_values,
            durable_values={
                "FB_AGENT_BOOTSTRAP_CLUSTER_ID": cluster_id,
                "POSTGRES_PASSWORD": durable_password,
            },
        )
        _atomic_write(output, _render_durable_values(durable_values))
        _validate_private_file(output, owner_uid=owner_uid)
        return cluster_id, True
    finally:
        os.close(lock_descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--browser-control-output", type=Path)
    parser.add_argument("--browser-maintenance-output", type=Path)
    parser.add_argument("--browser-autopause-output", type=Path)
    parser.add_argument("--browser-meta-api-output", type=Path)
    parser.add_argument("--browser-campaign-creator-output", type=Path)
    parser.add_argument("--browser-authority-output", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("bootstrap secret provisioning must run as root")
    try:
        cluster_id, created = provision(
            source=args.input,
            output=args.output,
            lock_path=args.lock,
        )
        if args.browser_control_output is not None:
            write_browser_control_env(
                bootstrap_secrets=args.output,
                output=args.browser_control_output,
            )
        if args.browser_maintenance_output is not None:
            write_scoped_browser_env(
                bootstrap_secrets=args.output,
                output=args.browser_maintenance_output,
                capability_key="BROWSER_MAINTENANCE_CAPABILITY_SECRET",
            )
        for scoped_output, source_key in (
            (
                args.browser_autopause_output,
                "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE",
            ),
            (
                args.browser_meta_api_output,
                "BROWSER_OPERATION_CAPABILITY_SECRET_META_API",
            ),
            (
                args.browser_campaign_creator_output,
                "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR",
            ),
        ):
            if scoped_output is None:
                continue
            write_scoped_browser_env(
                bootstrap_secrets=args.output,
                output=scoped_output,
                capability_key=source_key,
                output_key="BROWSER_OPERATION_CAPABILITY_SECRET",
            )
        if args.browser_authority_output is not None:
            write_scoped_browser_env(
                bootstrap_secrets=args.output,
                output=args.browser_authority_output,
                capability_key="BROWSER_AUTHORITY_CONSUMER_TOKEN",
            )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    action = "created" if created else "reused"
    print(f"Durable bootstrap identity {cluster_id} {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
