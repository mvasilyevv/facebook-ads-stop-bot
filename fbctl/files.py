"""Filesystem and dotenv contracts shared by all fbctl commands."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable

from fbctl.errors import FbctlError

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_REMOTE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$")
IMAGE_DIGEST = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


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
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise FbctlError(f"cannot read configuration file: {path}") from exc
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise FbctlError(f"invalid dotenv line {line_number} in {path}")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise FbctlError(f"invalid dotenv key on line {line_number} in {path}")
        if key in values:
            raise FbctlError(f"duplicate {key} in {path}")
        if "\r" in value or "\n" in value:
            raise FbctlError(f"invalid newline in {key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise FbctlError(f"{path} is missing required key {missing[0]}")
    return values


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
