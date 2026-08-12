"""Bounded bootstrap-only migration of the production Telegram identity."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from fbctl.adoption import VerifiedAdoptionBundle, verify_adoption_bundle
from fbctl.errors import FbctlError
from fbctl.files import (
    MAX_DOTENV_BYTES,
    PrivateFileSnapshot,
    parse_dotenv_payload,
    snapshot_private_file,
    trusted_shared_directory,
    unlink_unchanged_snapshot,
)

OIDC_KEYS = ("TELEGRAM_OIDC_CLIENT_ID", "TELEGRAM_OIDC_CLIENT_SECRET")
OWNER_KEY = "DESKTOP_OWNER_TELEGRAM_USER_ID"
IDENTITY_KEYS = (*OIDC_KEYS, OWNER_KEY)


@dataclass(frozen=True)
class HostIdentitySnapshot:
    required_uid: int
    legacy_included: bool
    canonical_values: dict[str, str]
    canonical_snapshot: PrivateFileSnapshot | None
    legacy_values: dict[str, str]
    legacy_snapshot: PrivateFileSnapshot | None
    adoption: VerifiedAdoptionBundle | None


@dataclass(frozen=True)
class IdentityResolution:
    values: dict[str, str]
    oidc_origin: str
    owner_origin: str
    legacy_cleanup_eligible: bool


def snapshot_host_identity(
    root: Path,
    adoption_bundle: Path | None = None,
    *,
    required_uid: int | None = None,
    include_legacy: bool = True,
) -> HostIdentitySnapshot:
    """Take one strict fd snapshot of each fixed host identity source."""

    owner_uid = os.geteuid() if required_uid is None else required_uid
    shared = root / "shared"
    with trusted_shared_directory(
        root,
        required_uid=owner_uid,
        missing_ok=True,
    ) as shared_fd:
        canonical_snapshot = (
            snapshot_private_file(
                shared / "source.env",
                label="canonical source environment",
                maximum=MAX_DOTENV_BYTES,
                required_uid=owner_uid,
                missing_ok=True,
                directory_fd=shared_fd,
            )
            if shared_fd is not None
            else None
        )
        legacy_snapshot = (
            snapshot_private_file(
                shared / ".env",
                label="legacy source environment",
                maximum=MAX_DOTENV_BYTES,
                required_uid=owner_uid,
                missing_ok=True,
                directory_fd=shared_fd,
            )
            if include_legacy and shared_fd is not None
            else None
        )
        canonical_values = _parse_snapshot(canonical_snapshot, "canonical source environment")
        legacy_values = _parse_snapshot(legacy_snapshot, "legacy source environment")
        adoption = None
        if adoption_bundle is not None and os.path.lexists(adoption_bundle):
            adoption = verify_adoption_bundle(
                adoption_bundle,
                required_uid=owner_uid,
                directory_fd=(
                    shared_fd
                    if shared_fd is not None and adoption_bundle.parent == shared
                    else None
                ),
            )
    return HostIdentitySnapshot(
        required_uid=owner_uid,
        legacy_included=include_legacy,
        canonical_values=canonical_values,
        canonical_snapshot=canonical_snapshot,
        legacy_values=legacy_values,
        legacy_snapshot=legacy_snapshot,
        adoption=adoption,
    )


def resolve_bootstrap_identity(
    *,
    explicit: dict[str, str],
    canonical: dict[str, str],
    legacy: dict[str, str],
    adoption_owner: str | None,
    migration_enabled: bool,
) -> IdentityResolution:
    """Resolve two indivisible identity units without ever mixing OIDC halves."""

    sources = (("explicit", explicit),)
    if migration_enabled:
        sources += (("canonical", canonical), ("legacy", legacy))

    oidc = next(
        (
            (origin, unit)
            for origin, values in sources
            if (unit := _oidc_unit(values, origin)) is not None
        ),
        None,
    )
    owner = next(
        (
            (origin, unit)
            for origin, values in sources
            if (unit := _owner_unit(values, origin)) is not None
        ),
        None,
    )
    if oidc is None:
        raise FbctlError("bootstrap identity is missing the Telegram OIDC pair")
    if owner is None and migration_enabled and adoption_owner is not None:
        owner = ("adoption", _validated_owner(adoption_owner, "adoption"))
    if owner is None:
        raise FbctlError("bootstrap identity is missing the desktop owner")

    oidc_origin, oidc_values = oidc
    owner_origin, owner_value = owner
    if adoption_owner is not None:
        verified_owner = _validated_owner(adoption_owner, "adoption")
        if owner_value != verified_owner:
            raise FbctlError("adoption bundle owner contract is invalid")

    values = {
        OIDC_KEYS[0]: oidc_values[0],
        OIDC_KEYS[1]: oidc_values[1],
        OWNER_KEY: owner_value,
    }
    legacy_cleanup_eligible = False
    if migration_enabled and any(legacy.get(key, "") for key in IDENTITY_KEYS):
        chosen_from_legacy = oidc_origin == "legacy" or owner_origin == "legacy"
        retry_from_canonical = oidc_origin == "canonical" or owner_origin == "canonical"
        legacy_matches = all(
            not legacy.get(key, "") or legacy[key] == values[key] for key in IDENTITY_KEYS
        )
        legacy_cleanup_eligible = legacy_matches and (chosen_from_legacy or retry_from_canonical)
    return IdentityResolution(values, oidc_origin, owner_origin, legacy_cleanup_eligible)


def remove_legacy_identity(snapshot: HostIdentitySnapshot) -> bool:
    if snapshot.legacy_snapshot is None:
        return False
    root = snapshot.legacy_snapshot.path.parent.parent
    try:
        with trusted_shared_directory(root, required_uid=snapshot.required_uid) as shared_fd:
            assert shared_fd is not None
            return unlink_unchanged_snapshot(
                snapshot.legacy_snapshot,
                directory_fd=shared_fd,
            )
    except FbctlError:
        return False


def snapshot_is_current(snapshot: PrivateFileSnapshot | None) -> bool:
    """Confirm a preflight pathname still names the exact verified inode."""

    if snapshot is None:
        return True
    try:
        directory = os.open(
            snapshot.path.parent,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        return False
    try:
        current = os.stat(snapshot.path.name, dir_fd=directory, follow_symlinks=False)
        return (
            current.st_dev == snapshot.device
            and current.st_ino == snapshot.inode
            and current.st_size == snapshot.size
            and current.st_uid == snapshot.uid
            and (current.st_mode & 0o777) == snapshot.mode
            and current.st_nlink == snapshot.links
            and current.st_mtime_ns == snapshot.modified_ns
            and current.st_ctime_ns == snapshot.changed_ns
        )
    except OSError:
        return False
    finally:
        os.close(directory)


def host_snapshot_is_current(
    snapshot: HostIdentitySnapshot,
    *,
    root: Path,
) -> bool:
    shared = root / "shared"
    try:
        with trusted_shared_directory(root, required_uid=snapshot.required_uid) as shared_fd:
            assert shared_fd is not None
            fixed_checks = [(snapshot.canonical_snapshot, shared / "source.env")]
            if snapshot.legacy_included:
                fixed_checks.append((snapshot.legacy_snapshot, shared / ".env"))
            for receipt, path in fixed_checks:
                if receipt is None:
                    try:
                        os.stat(path.name, dir_fd=shared_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        return False
                    return False
                if not _snapshot_is_current_at(receipt, shared_fd):
                    return False
            if snapshot.adoption is not None:
                adoption = snapshot.adoption.snapshot
                if adoption.path.parent == shared:
                    return _snapshot_is_current_at(adoption, shared_fd)
                return snapshot_is_current(adoption)
            return True
    except FbctlError:
        return False


def _snapshot_is_current_at(snapshot: PrivateFileSnapshot, directory_fd: int) -> bool:
    try:
        current = os.stat(snapshot.path.name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISREG(current.st_mode)
        and current.st_dev == snapshot.device
        and current.st_ino == snapshot.inode
        and current.st_size == snapshot.size
        and current.st_uid == snapshot.uid
        and stat.S_IMODE(current.st_mode) == snapshot.mode
        and current.st_nlink == snapshot.links
        and current.st_mtime_ns == snapshot.modified_ns
        and current.st_ctime_ns == snapshot.changed_ns
    )


def _parse_snapshot(snapshot: PrivateFileSnapshot | None, label: str) -> dict[str, str]:
    if snapshot is None:
        return {}
    return parse_dotenv_payload(snapshot.payload, label=label, maximum=MAX_DOTENV_BYTES)


def _oidc_unit(values: dict[str, str], origin: str) -> tuple[str, str] | None:
    present = [key in values for key in OIDC_KEYS]
    if not any(present):
        return None
    if not all(present):
        raise FbctlError(f"{origin} bootstrap identity has a partial Telegram OIDC pair")
    client_id = values[OIDC_KEYS[0]]
    secret = values[OIDC_KEYS[1]]
    if not client_id.isdigit() or len(secret) < 32:
        raise FbctlError(f"{origin} bootstrap identity has an invalid Telegram OIDC pair")
    return client_id, secret


def _owner_unit(values: dict[str, str], origin: str) -> str | None:
    if OWNER_KEY not in values:
        return None
    return _validated_owner(values[OWNER_KEY], origin)


def _validated_owner(value: str, origin: str) -> str:
    if not value.isdigit() or int(value) <= 0:
        raise FbctlError(f"{origin} bootstrap identity has an invalid desktop owner")
    return value
