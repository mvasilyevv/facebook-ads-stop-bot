"""Receipt-bound handling of the one approved Vision profile input."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import stat
import sys
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from fbctl.errors import FbctlError
from fbctl.files import trusted_shared_directory

PRODUCTION_ROOT = Path("/opt/fb-agent")
PRODUCTION_VISION_CONFIG = PRODUCTION_ROOT / "shared" / "vision-config"
PRODUCTION_DESKTOP_PROFILE_SEED = PRODUCTION_ROOT / "shared" / "desktop-profile-seed"
VISION_RUNTIME_UID = 1000
VISION_RUNTIME_GID = 1000

VISION_PROFILE_MARKER = ".fb-agent-vision-profile-v1"
VISION_PROFILE_MARKER_CONTENT = b"fb-agent-vision-profile-v1\n"
MAX_PROFILE_ENTRIES = 10_000
MAX_PROFILE_BYTES = 512 * 1024 * 1024
MAX_PROFILE_DEPTH = 32

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


@dataclass(frozen=True)
class DirectoryIdentity:
    device: int
    inode: int
    uid: int
    gid: int
    mode: int


@dataclass(frozen=True)
class TreeEntryReceipt:
    relative: tuple[str, ...]
    kind: str
    device: int
    inode: int
    size: int
    uid: int
    gid: int
    mode: int
    links: int
    modified_ns: int
    changed_ns: int
    sha256: str | None


@dataclass(frozen=True)
class VisionProfileTreeReceipt:
    path: Path
    label: str
    required_uid: int
    required_gid: int
    parent: DirectoryIdentity
    entries: tuple[TreeEntryReceipt, ...]


@dataclass(frozen=True)
class VisionProfileBootstrapInput:
    canonical_profile: Path
    desktop_profile_seed: Path
    canonical_required_uid: int
    canonical_required_gid: int
    seed_required_uid: int
    seed_required_gid: int
    canonical_receipt: VisionProfileTreeReceipt | None
    seed_receipt: VisionProfileTreeReceipt | None
    active_receipt: VisionProfileTreeReceipt
    seed_to_copy: Path | None
    seed_cleanup_receipt: VisionProfileTreeReceipt | None


def validate_bootstrap_vision_profile(
    *,
    canonical_profile: Path,
    desktop_profile_seed: Path | None,
    seed_required_uid: int,
    seed_required_gid: int,
    canonical_required_uid: int = VISION_RUNTIME_UID,
    canonical_required_gid: int = VISION_RUNTIME_GID,
) -> VisionProfileBootstrapInput:
    """Snapshot canonical retry state, otherwise the one explicit seed.

    Canonical state is authoritative.  A remaining seed is snapshotted only so
    the exact original tree can be removed after the full bootstrap succeeds.
    """

    if desktop_profile_seed is not None and _paths_overlap(canonical_profile, desktop_profile_seed):
        raise FbctlError("desktop profile seed overlaps managed Vision configuration")
    seed_was_supplied = desktop_profile_seed is not None
    seed_path = desktop_profile_seed or (
        canonical_profile.parent / PRODUCTION_DESKTOP_PROFILE_SEED.name
    )
    canonical = snapshot_profile_tree(
        canonical_profile,
        label="managed Vision configuration",
        required_uid=canonical_required_uid,
        required_gid=canonical_required_gid,
        missing_ok=True,
    )
    seed = snapshot_profile_tree(
        seed_path,
        label="desktop profile seed",
        required_uid=seed_required_uid,
        required_gid=seed_required_gid,
        missing_ok=True,
    )
    return _select_bootstrap_input(
        canonical_profile=canonical_profile,
        desktop_profile_seed=seed_path,
        seed_was_supplied=seed_was_supplied,
        canonical_required_uid=canonical_required_uid,
        canonical_required_gid=canonical_required_gid,
        seed_required_uid=seed_required_uid,
        seed_required_gid=seed_required_gid,
        canonical=canonical,
        seed=seed,
    )


def validate_production_vision_profile() -> VisionProfileBootstrapInput:
    """Read-only host preflight for the two fixed production paths."""

    with trusted_shared_directory(PRODUCTION_ROOT, required_uid=0) as shared_fd:
        assert shared_fd is not None
        canonical = snapshot_profile_tree(
            PRODUCTION_VISION_CONFIG,
            label="managed Vision configuration",
            required_uid=VISION_RUNTIME_UID,
            required_gid=VISION_RUNTIME_GID,
            missing_ok=True,
            directory_fd=shared_fd,
        )
        seed = snapshot_profile_tree(
            PRODUCTION_DESKTOP_PROFILE_SEED,
            label="desktop profile seed",
            required_uid=0,
            required_gid=0,
            missing_ok=True,
            directory_fd=shared_fd,
        )
    return _select_bootstrap_input(
        canonical_profile=PRODUCTION_VISION_CONFIG,
        desktop_profile_seed=PRODUCTION_DESKTOP_PROFILE_SEED,
        seed_was_supplied=True,
        canonical_required_uid=VISION_RUNTIME_UID,
        canonical_required_gid=VISION_RUNTIME_GID,
        seed_required_uid=0,
        seed_required_gid=0,
        canonical=canonical,
        seed=seed,
    )


def _select_bootstrap_input(
    *,
    canonical_profile: Path,
    desktop_profile_seed: Path,
    seed_was_supplied: bool,
    canonical_required_uid: int,
    canonical_required_gid: int,
    seed_required_uid: int,
    seed_required_gid: int,
    canonical: VisionProfileTreeReceipt | None,
    seed: VisionProfileTreeReceipt | None,
) -> VisionProfileBootstrapInput:
    if canonical is not None:
        return VisionProfileBootstrapInput(
            canonical_profile=canonical_profile,
            desktop_profile_seed=desktop_profile_seed,
            canonical_required_uid=canonical_required_uid,
            canonical_required_gid=canonical_required_gid,
            seed_required_uid=seed_required_uid,
            seed_required_gid=seed_required_gid,
            canonical_receipt=canonical,
            seed_receipt=seed,
            active_receipt=canonical,
            seed_to_copy=None,
            seed_cleanup_receipt=seed,
        )
    if seed is None or not seed_was_supplied:
        raise FbctlError("managed Vision configuration is absent and the approved seed is missing")
    return VisionProfileBootstrapInput(
        canonical_profile=canonical_profile,
        desktop_profile_seed=desktop_profile_seed,
        canonical_required_uid=canonical_required_uid,
        canonical_required_gid=canonical_required_gid,
        seed_required_uid=seed_required_uid,
        seed_required_gid=seed_required_gid,
        canonical_receipt=canonical,
        seed_receipt=seed,
        active_receipt=seed,
        seed_to_copy=desktop_profile_seed,
        seed_cleanup_receipt=seed,
    )


def snapshot_profile_tree(
    path: Path,
    *,
    label: str,
    required_uid: int,
    required_gid: int,
    missing_ok: bool = False,
    directory_fd: int | None = None,
) -> VisionProfileTreeReceipt | None:
    """Capture a bounded tree through no-follow file descriptors."""

    parent_fd: int | None = None
    root_fd: int | None = None
    try:
        parent_fd = (
            os.dup(directory_fd)
            if directory_fd is not None
            else _open_parent(path, label=label, missing_ok=missing_ok)
        )
        if parent_fd is None:
            return None
        parent = _directory_identity(os.fstat(parent_fd))
        try:
            root_fd = os.open(path.name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError as exc:
            if missing_ok:
                return None
            raise FbctlError(f"{label} is missing or unsafe") from exc
        except OSError as exc:
            raise FbctlError(f"{label} is missing or unsafe") from exc
        entries = _capture_open_tree(
            root_fd,
            label=label,
            required_uid=required_uid,
            required_gid=required_gid,
        )
        return VisionProfileTreeReceipt(
            path=path,
            label=label,
            required_uid=required_uid,
            required_gid=required_gid,
            parent=parent,
            entries=entries,
        )
    finally:
        if root_fd is not None:
            os.close(root_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def bootstrap_profile_is_current(profile: VisionProfileBootstrapInput) -> bool:
    """Recheck both expected path states under the lock before durable writes."""

    if not _optional_profile_receipt_is_current(
        path=profile.canonical_profile,
        label="managed Vision configuration",
        required_uid=profile.canonical_required_uid,
        required_gid=profile.canonical_required_gid,
        expected=profile.canonical_receipt,
    ):
        return False
    return _optional_profile_receipt_is_current(
        path=profile.desktop_profile_seed,
        label="desktop profile seed",
        required_uid=profile.seed_required_uid,
        required_gid=profile.seed_required_gid,
        expected=profile.seed_receipt,
    )


def _optional_profile_receipt_is_current(
    *,
    path: Path,
    label: str,
    required_uid: int,
    required_gid: int,
    expected: VisionProfileTreeReceipt | None,
) -> bool:
    try:
        current = snapshot_profile_tree(
            path,
            label=label,
            required_uid=required_uid,
            required_gid=required_gid,
            missing_ok=True,
        )
    except FbctlError:
        return False
    return current == expected


def copy_profile_from_receipt(
    receipt: VisionProfileTreeReceipt,
    destination: Path,
    *,
    uid: int,
    gid: int,
) -> VisionProfileTreeReceipt:
    """Copy only receipt-bound inodes, validate the temp tree, then publish."""

    temporary: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    try:
        if os.path.lexists(destination):
            raise FbctlError("managed Vision configuration appeared during bootstrap")
        parent_fd = _open_parent(
            destination,
            label="managed Vision configuration",
            missing_ok=False,
        )
        assert parent_fd is not None
        with ExitStack() as descriptors:
            descriptors.callback(os.close, parent_fd)
            temporary = Path(tempfile.mkdtemp(prefix=".vision-config.", dir=destination.parent))
            try:
                destination_fd = os.open(temporary.name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            except OSError:
                if _quarantine_unbound_temporary(temporary, parent_fd=parent_fd):
                    temporary = None
                raise
            descriptors.callback(os.close, destination_fd)
            opened_temporary = os.fstat(destination_fd)
            temporary_identity = (opened_temporary.st_dev, opened_temporary.st_ino)
            os.fchmod(destination_fd, 0o700)
            source_parent_fd, source_fd = _open_receipt_root(receipt)
            descriptors.callback(os.close, source_parent_fd)
            descriptors.callback(os.close, source_fd)
            _copy_open_tree(
                source_fd,
                destination_fd,
                receipt=receipt,
                uid=uid,
                gid=gid,
            )
            staged = snapshot_profile_tree(
                temporary,
                label="staged managed Vision configuration",
                required_uid=uid,
                required_gid=gid,
            )
            assert staged is not None
            _rename_noreplace(
                temporary.name,
                destination.name,
                source_parent_fd=parent_fd,
                destination_parent_fd=parent_fd,
            )
            published = snapshot_profile_tree(
                destination,
                label="managed Vision configuration",
                required_uid=uid,
                required_gid=gid,
            )
            assert published is not None
            if not _same_tree_after_root_rename(staged, published):
                raise FbctlError("managed Vision configuration changed during publication")
            return published
    except FbctlError:
        raise
    except OSError as exc:
        raise FbctlError("managed Vision configuration could not be prepared safely") from exc
    finally:
        if temporary is not None and temporary_identity is not None:
            _remove_private_temporary_tree(
                temporary,
                device=temporary_identity[0],
                inode=temporary_identity[1],
            )


def remove_profile_tree_receipt(receipt: VisionProfileTreeReceipt) -> bool:
    """Quarantine and remove only the exact snapshotted seed tree."""

    parent_fd: int | None = None
    quarantine_fd: int | None = None
    quarantine_name: str | None = None
    moved = False
    try:
        parent_fd = _open_parent(receipt.path, label=receipt.label, missing_ok=True)
        if parent_fd is None or _directory_identity(os.fstat(parent_fd)) != receipt.parent:
            return False
        try:
            current = os.stat(receipt.path.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            return False
        if not _entry_matches(current, receipt.entries[0], allow_root_rename=False):
            return False
        quarantine_name, quarantine_fd = _create_quarantine(
            parent_fd,
            uid=receipt.required_uid,
            gid=receipt.required_gid,
        )
        try:
            os.rename(
                receipt.path.name,
                "tree",
                src_dir_fd=parent_fd,
                dst_dir_fd=quarantine_fd,
            )
            moved = True
            os.fsync(parent_fd)
            os.fsync(quarantine_fd)
        except OSError:
            return False
        quarantined = snapshot_profile_tree(
            receipt.path.parent / quarantine_name / "tree",
            label=receipt.label,
            required_uid=receipt.required_uid,
            required_gid=receipt.required_gid,
        )
        assert quarantined is not None
        if not _same_tree_after_root_rename(receipt, quarantined):
            return False
        tree_fd = os.open("tree", _DIRECTORY_FLAGS, dir_fd=quarantine_fd)
        try:
            _remove_open_tree(tree_fd, expected=quarantined)
        finally:
            os.close(tree_fd)
        os.rmdir("tree", dir_fd=quarantine_fd)
        moved = False
        os.fsync(quarantine_fd)
        os.rmdir(quarantine_name, dir_fd=parent_fd)
        quarantine_name = None
        os.fsync(parent_fd)
        return True
    except (FbctlError, OSError):
        return False
    finally:
        if quarantine_fd is not None:
            os.close(quarantine_fd)
        if quarantine_name is not None and not moved and parent_fd is not None:
            try:
                os.rmdir(quarantine_name, dir_fd=parent_fd)
            except OSError:
                pass
        if parent_fd is not None:
            os.close(parent_fd)


def _capture_open_tree(
    root_fd: int,
    *,
    label: str,
    required_uid: int,
    required_gid: int,
) -> tuple[TreeEntryReceipt, ...]:
    entries: list[TreeEntryReceipt] = []
    total_bytes = 0

    def visit(directory_fd: int, relative: tuple[str, ...], depth: int) -> None:
        nonlocal total_bytes
        if depth > MAX_PROFILE_DEPTH:
            raise FbctlError(f"{label} exceeds safe bounds")
        before = os.fstat(directory_fd)
        _validate_entry_metadata(
            before,
            label=label,
            relative=relative,
            required_uid=required_uid,
            required_gid=required_gid,
            root=not relative,
        )
        entries.append(_entry_receipt(relative, "directory", before, None))
        names = _bounded_directory_names(
            directory_fd,
            maximum=MAX_PROFILE_ENTRIES - len(entries),
            error=f"{label} exceeds safe bounds",
        )
        for name in names:
            if not name or name in {".", ".."} or "/" in name or "\x00" in name:
                raise FbctlError(f"{label} contains an unsafe entry")
            child_relative = (*relative, name)
            try:
                metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise FbctlError(f"{label} contains an unsafe entry") from exc
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = _open_child_directory(directory_fd, name, label)
                try:
                    visit(child_fd, child_relative, depth + 1)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(metadata.st_mode):
                child_fd = _open_child_file(directory_fd, name, label)
                try:
                    opened = os.fstat(child_fd)
                    _validate_entry_metadata(
                        opened,
                        label=label,
                        relative=child_relative,
                        required_uid=required_uid,
                        required_gid=required_gid,
                        root=False,
                    )
                    payload_hash, size = _hash_open_file(
                        child_fd,
                        maximum=MAX_PROFILE_BYTES - total_bytes,
                    )
                    after = os.fstat(child_fd)
                    if not _stable_metadata(opened, after) or size != opened.st_size:
                        raise FbctlError(f"{label} changed while it was inspected")
                    total_bytes += size
                    if total_bytes > MAX_PROFILE_BYTES:
                        raise FbctlError(f"{label} exceeds safe bounds")
                    entries.append(_entry_receipt(child_relative, "file", after, payload_hash))
                finally:
                    os.close(child_fd)
            elif stat.S_ISLNK(metadata.st_mode):
                # XFCE держит внутри профиля свои ссылки (например
                # .config/xfce4/desktop/icons.screen.latest.rc на текущий экран).
                # Отвергать их значило бы, что любой bootstrap после первого
                # запуска рабочего стола падает. Ссылку не разыменовываем: в
                # чек записывается сама цель, и она обязана остаться внутри дерева.
                _validate_entry_metadata(
                    metadata,
                    label=label,
                    relative=child_relative,
                    required_uid=required_uid,
                    required_gid=required_gid,
                    root=False,
                )
                target = _readlink_inside_tree(
                    directory_fd,
                    name,
                    relative=child_relative,
                    label=label,
                )
                entries.append(
                    _entry_receipt(
                        child_relative,
                        "symlink",
                        metadata,
                        hashlib.sha256(target.encode("utf-8")).hexdigest(),
                    )
                )
            else:
                raise FbctlError(f"{label} contains an unsafe entry")
            if len(entries) > MAX_PROFILE_ENTRIES:
                raise FbctlError(f"{label} exceeds safe bounds")
        after = os.fstat(directory_fd)
        if not _stable_metadata(before, after):
            raise FbctlError(f"{label} changed while it was inspected")

    visit(root_fd, (), 0)
    marker = next(
        (entry for entry in entries if entry.relative == (VISION_PROFILE_MARKER,)),
        None,
    )
    expected_hash = hashlib.sha256(VISION_PROFILE_MARKER_CONTENT).hexdigest()
    if (
        marker is None
        or marker.kind != "file"
        or marker.mode != 0o600
        or marker.links != 1
        or marker.size != len(VISION_PROFILE_MARKER_CONTENT)
        or marker.sha256 != expected_hash
    ):
        raise FbctlError(f"{label} marker is invalid")
    return tuple(sorted(entries, key=lambda item: item.relative))


def _copy_open_tree(
    source_fd: int,
    destination_fd: int,
    *,
    receipt: VisionProfileTreeReceipt,
    uid: int,
    gid: int,
) -> None:
    expected = {entry.relative: entry for entry in receipt.entries}
    total_copied = 0

    def copy_directory(src_fd: int, dst_fd: int, relative: tuple[str, ...]) -> None:
        nonlocal total_copied
        directory_receipt = expected[relative]
        if not _entry_matches(os.fstat(src_fd), directory_receipt, allow_root_rename=False):
            raise FbctlError("desktop profile seed changed before it was copied")
        children = sorted(
            (
                entry
                for entry in receipt.entries
                if len(entry.relative) == len(relative) + 1 and entry.relative[:-1] == relative
            ),
            key=lambda item: item.relative,
        )
        expected_names = [entry.relative[-1] for entry in children]
        if (
            _bounded_directory_names(
                src_fd,
                maximum=len(expected_names),
                error="desktop profile seed changed before it was copied",
            )
            != expected_names
        ):
            raise FbctlError("desktop profile seed changed before it was copied")
        for child in children:
            name = child.relative[-1]
            if child.kind == "directory":
                source_child = _open_child_directory(src_fd, name, receipt.label)
                try:
                    if not _entry_matches(os.fstat(source_child), child, allow_root_rename=False):
                        raise FbctlError("desktop profile seed changed before it was copied")
                    os.mkdir(name, 0o700, dir_fd=dst_fd)
                    destination_child = os.open(name, _DIRECTORY_FLAGS, dir_fd=dst_fd)
                    try:
                        copy_directory(source_child, destination_child, child.relative)
                    finally:
                        os.close(destination_child)
                finally:
                    os.close(source_child)
            elif child.kind == "symlink":
                # Seed приходит из репозитория и ссылок содержать не должен;
                # копировать их сюда — значит переносить чужую цель вслепую.
                raise FbctlError("desktop profile seed must not contain symlinks")
            else:
                source_child = _open_child_file(src_fd, name, receipt.label)
                destination_child: int | None = None
                try:
                    before = os.fstat(source_child)
                    if not _entry_matches(before, child, allow_root_rename=False):
                        raise FbctlError("desktop profile seed changed before it was copied")
                    destination_child = os.open(
                        name,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=dst_fd,
                    )
                    digest = hashlib.sha256()
                    copied = 0
                    while copied < child.size:
                        remaining = child.size - copied
                        chunk = os.read(source_child, min(1024 * 1024, remaining))
                        if not chunk:
                            raise FbctlError("desktop profile seed changed before it was copied")
                        if len(chunk) > remaining:
                            raise FbctlError("desktop profile seed changed before it was copied")
                        digest.update(chunk)
                        copied += len(chunk)
                        total_copied += len(chunk)
                        if total_copied > MAX_PROFILE_BYTES:
                            raise FbctlError("desktop profile seed exceeds safe bounds")
                        _write_all(destination_child, chunk)
                    if os.read(source_child, 1):
                        raise FbctlError("desktop profile seed changed before it was copied")
                    after = os.fstat(source_child)
                    if (
                        not _stable_metadata(before, after)
                        or copied != child.size
                        or digest.hexdigest() != child.sha256
                    ):
                        raise FbctlError("desktop profile seed changed before it was copied")
                    os.fchmod(destination_child, 0o600)
                    os.fchown(destination_child, uid, gid)
                    os.fsync(destination_child)
                finally:
                    if destination_child is not None:
                        os.close(destination_child)
                    os.close(source_child)
        if (
            _bounded_directory_names(
                src_fd,
                maximum=len(expected_names),
                error="desktop profile seed changed before it was copied",
            )
            != expected_names
        ):
            raise FbctlError("desktop profile seed changed before it was copied")
        if not _entry_matches(os.fstat(src_fd), directory_receipt, allow_root_rename=False):
            raise FbctlError("desktop profile seed changed before it was copied")
        os.fchmod(dst_fd, 0o700)
        os.fchown(dst_fd, uid, gid)
        os.fsync(dst_fd)

    copy_directory(source_fd, destination_fd, ())


def _remove_open_tree(root_fd: int, *, expected: VisionProfileTreeReceipt) -> None:
    entries = {entry.relative: entry for entry in expected.entries}

    def remove_directory(directory_fd: int, relative: tuple[str, ...]) -> None:
        if relative in entries and not _entry_matches(
            os.fstat(directory_fd), entries[relative], allow_root_rename=True
        ):
            raise FbctlError("desktop profile seed changed during cleanup")
        children = sorted(
            (entry for entry in expected.entries if entry.relative[:-1] == relative),
            key=lambda item: item.relative,
            reverse=True,
        )
        immediate = [entry for entry in children if len(entry.relative) == len(relative) + 1]
        expected_names = sorted(entry.relative[-1] for entry in immediate)
        if (
            _bounded_directory_names(
                directory_fd,
                maximum=len(expected_names),
                error="desktop profile seed changed during cleanup",
            )
            != expected_names
        ):
            raise FbctlError("desktop profile seed changed during cleanup")
        for child in immediate:
            name = child.relative[-1]
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not _entry_matches(current, child, allow_root_rename=False):
                raise FbctlError("desktop profile seed changed during cleanup")
            if child.kind == "directory":
                child_fd = _open_child_directory(directory_fd, name, expected.label)
                try:
                    remove_directory(child_fd, child.relative)
                finally:
                    os.close(child_fd)
                os.rmdir(name, dir_fd=directory_fd)
            else:
                os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)

    remove_directory(root_fd, ())


def _open_receipt_root(receipt: VisionProfileTreeReceipt) -> tuple[int, int]:
    parent_fd = _open_parent(receipt.path, label=receipt.label, missing_ok=False)
    assert parent_fd is not None
    try:
        if _directory_identity(os.fstat(parent_fd)) != receipt.parent:
            raise FbctlError("desktop profile seed changed before it was copied")
        try:
            root_fd = os.open(receipt.path.name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise FbctlError("desktop profile seed changed before it was copied") from exc
        if not _entry_matches(os.fstat(root_fd), receipt.entries[0], allow_root_rename=False):
            os.close(root_fd)
            raise FbctlError("desktop profile seed changed before it was copied")
        return parent_fd, root_fd
    except Exception:
        os.close(parent_fd)
        raise


def _open_parent(path: Path, *, label: str, missing_ok: bool) -> int | None:
    try:
        return os.open(path.parent, _DIRECTORY_FLAGS)
    except FileNotFoundError as exc:
        if missing_ok:
            return None
        raise FbctlError(f"{label} has an unsafe parent directory") from exc
    except OSError as exc:
        raise FbctlError(f"{label} has an unsafe parent directory") from exc


def _open_child_directory(parent_fd: int, name: str, label: str) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise FbctlError(f"{label} contains an unsafe entry") from exc


def _open_child_file(parent_fd: int, name: str, label: str) -> int:
    try:
        return os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise FbctlError(f"{label} contains an unsafe entry") from exc


def _readlink_inside_tree(
    directory_fd: int,
    name: str,
    *,
    relative: tuple[str, ...],
    label: str,
) -> str:
    """Прочитать цель ссылки и убедиться, что она не выходит за дерево.

    Ссылку не разыменовываем, поэтому проверяем сам текст цели: абсолютный путь
    уводит наружу сразу, а относительный — если `..` поднимается выше корня
    профиля. Ссылка на соседний файл внутри профиля безопасна и остаётся.
    """
    try:
        target = os.readlink(name, dir_fd=directory_fd)
    except OSError as exc:
        raise FbctlError(f"{label} contains an unsafe entry") from exc
    if not target or target.startswith("/") or "\x00" in target:
        raise FbctlError(f"{label} contains an unsafe link")
    # Глубина каталога, в котором лежит сама ссылка, считая от корня профиля.
    depth = len(relative) - 1
    for part in target.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                raise FbctlError(f"{label} contains an unsafe link")
        else:
            depth += 1
    return target


def _validate_entry_metadata(
    metadata: os.stat_result,
    *,
    label: str,
    relative: tuple[str, ...],
    required_uid: int,
    required_gid: int,
    root: bool,
) -> None:
    if not (
        stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
    ):
        raise FbctlError(f"{label} contains an unsafe entry")
    mode = stat.S_IMODE(metadata.st_mode)
    if metadata.st_uid != required_uid or metadata.st_gid != required_gid:
        raise FbctlError(f"{label} has invalid ownership")
    if root and (not stat.S_ISDIR(metadata.st_mode) or mode != 0o700):
        raise FbctlError(f"{label} is missing or unsafe")
    # Права символической ссылки на Linux ничего не значат и всегда 0777,
    # поэтому проверка на world-writable к ней неприменима. Безопасность
    # ссылки определяется её целью — см. _readlink_inside_tree.
    if not stat.S_ISLNK(metadata.st_mode) and mode & 0o022:
        raise FbctlError(f"{label} contains an unsafe entry")
    if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
        raise FbctlError(f"{label} contains an unsafe entry")
    if relative == (VISION_PROFILE_MARKER,) and mode != 0o600:
        raise FbctlError(f"{label} marker is invalid")


def _entry_receipt(
    relative: tuple[str, ...],
    kind: str,
    metadata: os.stat_result,
    digest: str | None,
) -> TreeEntryReceipt:
    return TreeEntryReceipt(
        relative=relative,
        kind=kind,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        mode=stat.S_IMODE(metadata.st_mode),
        links=metadata.st_nlink,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
        sha256=digest,
    )


def _entry_matches(
    metadata: os.stat_result,
    receipt: TreeEntryReceipt,
    *,
    allow_root_rename: bool,
) -> bool:
    basic = (
        metadata.st_dev == receipt.device
        and metadata.st_ino == receipt.inode
        and metadata.st_size == receipt.size
        and metadata.st_uid == receipt.uid
        and metadata.st_gid == receipt.gid
        and stat.S_IMODE(metadata.st_mode) == receipt.mode
        and metadata.st_nlink == receipt.links
        and metadata.st_mtime_ns == receipt.modified_ns
        and (
            receipt.kind == "directory"
            and stat.S_ISDIR(metadata.st_mode)
            or receipt.kind == "file"
            and stat.S_ISREG(metadata.st_mode)
            or receipt.kind == "symlink"
            and stat.S_ISLNK(metadata.st_mode)
        )
    )
    return basic and (allow_root_rename or metadata.st_ctime_ns == receipt.changed_ns)


def _same_tree_after_root_rename(
    before: VisionProfileTreeReceipt,
    after: VisionProfileTreeReceipt,
) -> bool:
    if len(before.entries) != len(after.entries):
        return False
    for old, new in zip(before.entries, after.entries, strict=True):
        if old.relative != new.relative:
            return False
        if old.relative == ():
            if (
                old.kind,
                old.device,
                old.inode,
                old.size,
                old.uid,
                old.gid,
                old.mode,
                old.links,
                old.modified_ns,
                old.sha256,
            ) != (
                new.kind,
                new.device,
                new.inode,
                new.size,
                new.uid,
                new.gid,
                new.mode,
                new.links,
                new.modified_ns,
                new.sha256,
            ):
                return False
        elif old != new:
            return False
    return True


def _stable_metadata(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_uid == after.st_uid
        and before.st_gid == after.st_gid
        and before.st_mode == after.st_mode
        and before.st_nlink == after.st_nlink
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _directory_identity(metadata: os.stat_result) -> DirectoryIdentity:
    if not stat.S_ISDIR(metadata.st_mode):
        raise FbctlError("Vision profile parent directory is unsafe")
    return DirectoryIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        mode=stat.S_IMODE(metadata.st_mode),
    )


def _hash_open_file(descriptor: int, *, maximum: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - size))
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
        if size > maximum:
            raise FbctlError("Vision profile exceeds safe bounds")
    return digest.hexdigest(), size


def _bounded_directory_names(
    directory_fd: int,
    *,
    maximum: int,
    error: str,
) -> list[str]:
    """Read at most ``maximum`` entry names from an already-open directory."""

    names: list[str] = []
    try:
        with os.scandir(directory_fd) as iterator:
            for entry in iterator:
                if len(names) >= maximum:
                    raise FbctlError(error)
                names.append(entry.name)
    except FbctlError:
        raise
    except OSError as exc:
        raise FbctlError(error) from exc
    return sorted(names)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _create_quarantine(parent_fd: int, *, uid: int, gid: int) -> tuple[str, int]:
    for _attempt in range(16):
        name = f".fbctl-profile-cleanup-{os.getpid()}-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        descriptor: int | None = None
        try:
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            os.fchmod(descriptor, 0o700)
            os.fchown(descriptor, uid, gid)
            metadata = os.fstat(descriptor)
            if (
                metadata.st_uid != uid
                or metadata.st_gid != gid
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise OSError("unsafe quarantine")
            return name, descriptor
        except OSError:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
    raise OSError("cannot create profile cleanup quarantine")


def _remove_private_temporary_tree(path: Path, *, device: int, inode: int) -> None:
    parent_fd: int | None = None
    root_fd: int | None = None
    try:
        parent_fd = os.open(path.parent, _DIRECTORY_FLAGS)
        metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            metadata.st_dev != device
            or metadata.st_ino != inode
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            return
        root_fd = os.open(path.name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        _remove_untrusted_open_tree(root_fd)
        os.rmdir(path.name, dir_fd=parent_fd)
    except (FbctlError, OSError):
        return
    finally:
        if root_fd is not None:
            os.close(root_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _quarantine_unbound_temporary(path: Path, *, parent_fd: int) -> bool:
    """Move an unbound temp aside without deleting a possible substitution."""

    for _attempt in range(16):
        quarantine = f".fbctl-profile-cleanup-unbound-{os.getpid()}-{secrets.token_hex(8)}"
        try:
            _rename_noreplace(
                path.name,
                quarantine,
                source_parent_fd=parent_fd,
                destination_parent_fd=parent_fd,
            )
        except FbctlError:
            continue
        try:
            os.fsync(parent_fd)
        except OSError:
            pass
        return True
    return False


def _remove_untrusted_open_tree(directory_fd: int) -> None:
    for name in _bounded_directory_names(
        directory_fd,
        maximum=MAX_PROFILE_ENTRIES,
        error="temporary Vision configuration exceeds safe bounds",
    ):
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            try:
                _remove_untrusted_open_tree(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _rename_noreplace(
    source: str,
    destination: str,
    *,
    source_parent_fd: int,
    destination_parent_fd: int,
) -> None:
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise FbctlError("atomic Vision profile publication is unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            source_parent_fd,
            os.fsencode(source),
            destination_parent_fd,
            os.fsencode(destination),
            1,  # RENAME_NOREPLACE
        )
        if result != 0:
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FbctlError("managed Vision configuration appeared during bootstrap")
            raise FbctlError("managed Vision configuration could not be published safely")
        return
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx_np = getattr(libc, "renameatx_np", None)
        if renameatx_np is None:
            raise FbctlError("atomic Vision profile publication is unavailable")
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            source_parent_fd,
            os.fsencode(source),
            destination_parent_fd,
            os.fsencode(destination),
            0x00000004,  # RENAME_EXCL
        )
        if result != 0:
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FbctlError("managed Vision configuration appeared during bootstrap")
            raise FbctlError("managed Vision configuration could not be published safely")
        return
    raise FbctlError("atomic Vision profile publication is unavailable")


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents
