"""Filesystem and dotenv contracts shared by all fbctl commands."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from fbctl.errors import FbctlError

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_REMOTE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$")
IMAGE_DIGEST = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
MAX_DOTENV_BYTES = 2_000_000


@dataclass(frozen=True)
class PrivateFileSnapshot:
    """Exact bounded bytes and inode metadata verified through one open fd."""

    path: Path
    payload: bytes
    device: int
    inode: int
    size: int
    uid: int
    mode: int
    links: int
    modified_ns: int
    changed_ns: int


def require_absolute_path(path: Path, *, label: str) -> Path:
    raw = os.fspath(path)
    if (
        not path.is_absolute()
        or raw == "/"
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise FbctlError(f"{label} must be a canonical non-root absolute path")
    return path


def require_release_id(value: str) -> str:
    if not SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise FbctlError("release id contains unsupported characters")
    return value


def require_remote(value: str) -> str:
    if not SAFE_REMOTE.fullmatch(value):
        raise FbctlError("host must be an explicit user@host target")
    return value


def parse_dotenv(path: Path, *, required: Iterable[str] = ()) -> dict[str, str]:
    try:
        payload = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise FbctlError(f"cannot read configuration file: {path}") from exc
    return parse_dotenv_payload(payload, label=os.fspath(path), required=required)


def parse_dotenv_payload(
    payload: bytes,
    *,
    label: str,
    required: Iterable[str] = (),
    maximum: int = MAX_DOTENV_BYTES,
) -> dict[str, str]:
    if len(payload) > maximum or b"\x00" in payload:
        raise FbctlError(f"{label} exceeds its size limit or contains NUL")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise FbctlError(f"{label} is not valid UTF-8") from exc
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise FbctlError(f"invalid dotenv line {line_number} in {label}")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise FbctlError(f"invalid dotenv key on line {line_number} in {label}")
        if key in values:
            raise FbctlError(f"duplicate {key} in {label}")
        if "\r" in value or "\n" in value:
            raise FbctlError(f"invalid newline in {key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise FbctlError(f"{label} is missing required key {missing[0]}")
    return values


def snapshot_private_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    required_uid: int | None,
    missing_ok: bool = False,
    directory_fd: int | None = None,
) -> PrivateFileSnapshot | None:
    """Read a private regular file without following or reopening its pathname."""

    directory: int | None = None
    if directory_fd is None:
        parent_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            directory = os.open(path.parent, parent_flags)
        except FileNotFoundError as exc:
            if missing_ok:
                return None
            raise FbctlError(f"{label} is missing") from exc
        except OSError as exc:
            raise FbctlError(f"{label} has an unsafe parent directory") from exc
        directory_fd = directory
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path.name, flags, dir_fd=directory_fd)
    except FileNotFoundError as exc:
        if directory is not None:
            os.close(directory)
        if missing_ok:
            return None
        raise FbctlError(f"{label} is missing") from exc
    except OSError as exc:
        if directory is not None:
            os.close(directory)
        raise FbctlError(f"{label} is unsafe or unreadable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > maximum
            or (required_uid is not None and before.st_uid != required_uid)
        ):
            raise FbctlError(f"{label} is not a private single-owner file")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size == len(payload)
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns
            and after.st_nlink == 1
        )
        if len(payload) > maximum or not stable:
            raise FbctlError(f"{label} changed while it was read")
        return PrivateFileSnapshot(
            path=path,
            payload=payload,
            device=after.st_dev,
            inode=after.st_ino,
            size=after.st_size,
            uid=after.st_uid,
            mode=stat.S_IMODE(after.st_mode),
            links=after.st_nlink,
            modified_ns=after.st_mtime_ns,
            changed_ns=after.st_ctime_ns,
        )
    finally:
        os.close(descriptor)
        if directory is not None:
            os.close(directory)


def unlink_unchanged_snapshot(
    snapshot: PrivateFileSnapshot,
    *,
    directory_fd: int | None = None,
) -> bool:
    """Quarantine, revalidate, then remove only the snapshotted inode."""

    directory: int | None = None
    if directory_fd is None:
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
        directory_fd = directory
    quarantine_fd: int | None = None
    quarantine_name: str | None = None
    moved = False
    try:
        try:
            current = os.stat(snapshot.path.name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError:
            return False
        expected = (
            snapshot.device,
            snapshot.inode,
            snapshot.size,
            snapshot.uid,
            snapshot.mode,
            snapshot.links,
            snapshot.modified_ns,
            snapshot.changed_ns,
        )
        actual = (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_uid,
            stat.S_IMODE(current.st_mode),
            current.st_nlink,
            current.st_mtime_ns,
            current.st_ctime_ns,
        )
        if not stat.S_ISREG(current.st_mode) or actual != expected:
            return False
        try:
            quarantine_name, quarantine_fd = _create_private_quarantine(
                directory_fd,
                required_uid=snapshot.uid,
            )
        except OSError:
            return False
        try:
            os.rename(
                snapshot.path.name,
                "snapshot",
                src_dir_fd=directory_fd,
                dst_dir_fd=quarantine_fd,
            )
            moved = True
            os.fsync(quarantine_fd)
            os.fsync(directory_fd)
        except OSError:
            return False

        if not _quarantined_snapshot_matches(snapshot, directory_fd=quarantine_fd):
            _restore_quarantined_snapshot(
                snapshot.path.name,
                parent_fd=directory_fd,
                quarantine_fd=quarantine_fd,
            )
            return False

        try:
            os.unlink("snapshot", dir_fd=quarantine_fd)
            moved = False
            os.fsync(quarantine_fd)
            os.rmdir(quarantine_name, dir_fd=directory_fd)
            quarantine_name = None
            os.fsync(directory_fd)
        except OSError:
            return False
        return True
    finally:
        if quarantine_fd is not None:
            os.close(quarantine_fd)
        if quarantine_name is not None and not moved:
            try:
                os.rmdir(quarantine_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except OSError:
                pass
        if directory is not None:
            os.close(directory)


def _create_private_quarantine(directory_fd: int, *, required_uid: int) -> tuple[str, int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for _attempt in range(16):
        name = f".fbctl-cleanup-{os.getpid()}-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=directory_fd)
        except FileExistsError:
            continue
        try:
            os.chmod(name, 0o700, dir_fd=directory_fd, follow_symlinks=False)
            descriptor = os.open(name, flags, dir_fd=directory_fd)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != required_uid
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                os.close(descriptor)
                raise OSError("quarantine directory is unsafe")
            os.fsync(directory_fd)
            return name, descriptor
        except OSError:
            try:
                os.rmdir(name, dir_fd=directory_fd)
            except OSError:
                pass
            raise
    raise OSError("cannot allocate cleanup quarantine")


def _quarantined_snapshot_matches(
    snapshot: PrivateFileSnapshot,
    *,
    directory_fd: int,
) -> bool:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open("snapshot", flags, dir_fd=directory_fd)
    except OSError:
        return False
    try:
        try:
            before = os.fstat(descriptor)
            expected = (
                snapshot.device,
                snapshot.inode,
                snapshot.size,
                snapshot.uid,
                snapshot.mode,
                snapshot.links,
                snapshot.modified_ns,
            )
            actual = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_uid,
                stat.S_IMODE(before.st_mode),
                before.st_nlink,
                before.st_mtime_ns,
            )
            if not stat.S_ISREG(before.st_mode) or actual != expected:
                return False
            chunks: list[bytes] = []
            remaining = snapshot.size + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            stable = (
                before.st_dev == after.st_dev
                and before.st_ino == after.st_ino
                and before.st_size == after.st_size
                and before.st_uid == after.st_uid
                and before.st_mode == after.st_mode
                and before.st_nlink == after.st_nlink
                and before.st_mtime_ns == after.st_mtime_ns
                and before.st_ctime_ns == after.st_ctime_ns
            )
            return stable and b"".join(chunks) == snapshot.payload
        except OSError:
            return False
    finally:
        os.close(descriptor)


def _restore_quarantined_snapshot(
    original_name: str,
    *,
    parent_fd: int,
    quarantine_fd: int,
) -> bool:
    """Restore without clobbering, retaining quarantine as a durable safety link."""

    try:
        os.link(
            "snapshot",
            original_name,
            src_dir_fd=quarantine_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        os.fsync(parent_fd)
        os.fsync(quarantine_fd)
        return True
    except OSError:
        return False


@contextmanager
def trusted_shared_directory(
    root: Path,
    *,
    required_uid: int,
    create: bool = False,
    missing_ok: bool = False,
) -> Iterator[int | None]:
    """Open the fixed host root/shared chain without following either component."""

    root = require_absolute_path(root, label="root")
    root_fd = _open_trusted_directory(
        root,
        label="production root",
        mode=0o755,
        required_uid=required_uid,
        create=create,
        missing_ok=missing_ok,
    )
    if root_fd is None:
        yield None
        return
    shared_fd: int | None = None
    try:
        shared_fd = _open_trusted_directory(
            "shared",
            label="production shared directory",
            mode=0o700,
            required_uid=required_uid,
            create=create,
            missing_ok=missing_ok,
            directory_fd=root_fd,
        )
        yield shared_fd
    finally:
        if shared_fd is not None:
            os.close(shared_fd)
        os.close(root_fd)


def _open_trusted_directory(
    path: str | Path,
    *,
    label: str,
    mode: int,
    required_uid: int,
    create: bool,
    missing_ok: bool,
    directory_fd: int | None = None,
) -> int | None:
    created = False
    if create:
        try:
            os.mkdir(path, mode, dir_fd=directory_fd)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise FbctlError(f"{label} cannot be created safely") from exc
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, dir_fd=directory_fd)
    except FileNotFoundError as exc:
        if missing_ok:
            return None
        raise FbctlError(f"{label} is missing") from exc
    except OSError as exc:
        raise FbctlError(f"{label} is unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if created and metadata.st_uid == required_uid:
            os.fchmod(descriptor, mode)
            metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != required_uid
            or stat.S_IMODE(metadata.st_mode) != mode
        ):
            raise FbctlError(f"{label} must be an owned directory with mode {mode:o}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def require_private_file(path: Path, *, mode: int = 0o600) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FbctlError(f"required private file is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FbctlError(f"required private file is unsafe: {path}")
    if stat.S_IMODE(metadata.st_mode) != mode:
        raise FbctlError(f"{path} must have mode {mode:o}")
    return path


def require_directory(path: Path, *, mode: int | None = None) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FbctlError(f"required directory is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FbctlError(f"required directory is unsafe: {path}")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise FbctlError(f"{path} must have mode {mode:o}")
    return path


def atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, payload: dict[str, Any], *, mode: int = 0o600) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write(path, rendered, mode=mode)


def atomic_symlink(*, target: str, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = link.parent / f".{link.name}.{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
        _fsync_directory(link.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
