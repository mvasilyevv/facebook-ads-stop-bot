#!/usr/bin/env python3
"""Durable first-cluster transaction marker for crash-safe bootstrap retries."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CLUSTER_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]+$")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def expected_state(
    *,
    cluster_id: str,
    postgres_volume: str,
    platform_network: str,
) -> dict[str, Any]:
    if not CLUSTER_ID_RE.fullmatch(cluster_id):
        raise ValueError("invalid bootstrap cluster id")
    for name, value in (
        ("postgres volume", postgres_volume),
        ("platform network", platform_network),
    ):
        if not RESOURCE_RE.fullmatch(value):
            raise ValueError(f"invalid {name}")
    return {
        "version": 1,
        "cluster_id": cluster_id,
        "postgres_volume": postgres_volume,
        "platform_network": platform_network,
        "stage": "database_owned",
    }


def read_state(path: Path, *, owner_uid: int = 0) -> dict[str, Any] | None:
    if not path.exists() and not path.is_symlink():
        return None
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("bootstrap state must be a regular file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError("bootstrap state must have mode 600")
    if metadata.st_uid != owner_uid:
        raise ValueError("bootstrap state has an unexpected owner")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("bootstrap state is invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("bootstrap state must be an object")
    return data


def record_owned(
    path: Path,
    *,
    cluster_id: str,
    postgres_volume: str,
    platform_network: str,
    owner_uid: int = 0,
) -> bool:
    expected = expected_state(
        cluster_id=cluster_id,
        postgres_volume=postgres_volume,
        platform_network=platform_network,
    )
    existing = read_state(path, owner_uid=owner_uid)
    if existing is not None:
        comparable = {key: existing.get(key) for key in expected}
        if comparable != expected:
            raise ValueError("bootstrap state belongs to a different cluster or resource set")
        return False

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.lstat().st_uid != owner_uid:
        raise ValueError("bootstrap state directory has an unexpected owner")
    document = expected | {"recorded_at": datetime.now(UTC).isoformat()}
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def validate_owned(
    path: Path,
    *,
    cluster_id: str,
    postgres_volume: str,
    platform_network: str,
    owner_uid: int = 0,
) -> bool:
    expected = expected_state(
        cluster_id=cluster_id,
        postgres_volume=postgres_volume,
        platform_network=platform_network,
    )
    existing = read_state(path, owner_uid=owner_uid)
    if existing is None:
        return False
    comparable = {key: existing.get(key) for key in expected}
    if comparable != expected:
        raise ValueError("bootstrap state belongs to a different cluster or resource set")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("record-owned", "validate-owned"))
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--cluster-id", required=True)
    parser.add_argument("--postgres-volume", required=True)
    parser.add_argument("--platform-network", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("bootstrap state management must run as root")
    try:
        if args.command == "record-owned":
            created = record_owned(
                args.state,
                cluster_id=args.cluster_id,
                postgres_volume=args.postgres_volume,
                platform_network=args.platform_network,
            )
            print("created" if created else "reused")
            return 0
        owned = validate_owned(
            args.state,
            cluster_id=args.cluster_id,
            postgres_volume=args.postgres_volume,
            platform_network=args.platform_network,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print("owned" if owned else "absent")
    return 0 if owned else 3


if __name__ == "__main__":
    raise SystemExit(main())
