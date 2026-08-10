#!/usr/bin/env python3
"""Crash-consistent, versioned application release state.

The only application commit point is the ``shared/active-state`` symlink.
Every target directory is immutable and contains the release manifest, app
environment, color and release-directory link.  A durable journal records the
in-flight transaction so boot/runtime reconciliation can always converge to
the state selected by that single pointer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

SCHEMA = "fb-agent-release-state/v1"
JOURNAL_SCHEMA = "fb-agent-release-transaction/v1"
ROLLBACK_MARKER_SCHEMA = "fb-agent-rollback-failure/v1"
RELEASE_MANIFEST_SCHEMA = "fb-agent-release-content/v1"
RUNTIME_STATE_SCHEMA = "fb-agent-runtime-state/v1"
DESKTOP_STATE_DIGEST_SCHEMA = "fb-agent-desktop-state-content/v1"
RELEASE_MANIFEST_NAME = ".fb-agent-source-manifest.json"
EFFECTIVE_CONFIG_FINGERPRINT_NAME = ".fb-agent-effective-config.sha256"
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
DESKTOP_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
IMMUTABLE_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
ALLOWED_COLORS = {"blue", "green"}
STATE_METADATA_BOUND_FIELDS = (
    "schema",
    "release_id",
    "color",
    "release_root",
    "release_dir",
    "effective_config_fingerprint",
    "prepared_at",
)
STATE_METADATA_FIELDS = frozenset((*STATE_METADATA_BOUND_FIELDS, "state_id", "content_digest"))
RELEASE_EXCLUDED_TOP_LEVEL = {
    ".env",
    ".coverage",
    ".DS_Store",
    ".git",
    ".hypothesis",
    ".logs",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "data",
    "production.env",
    "release-images.env",
    ".fb-agent-release",
    EFFECTIVE_CONFIG_FINGERPRINT_NAME,
    RELEASE_MANIFEST_NAME,
}
RELEASE_EXCLUDED_ANY_DIR = {
    ".experimental-vitest-cache",
    ".hypothesis",
    ".mypy_cache",
    ".ruff_cache",
    ".tanstack",
    ".tox",
    "__pycache__",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "playwright-report",
    "storybook-static",
    "test-results",
}
STAGES = {
    "prepared": 0,
    "candidate_started": 1,
    "route_switched": 2,
    "workers_handed_off": 3,
    "accepted": 4,
    "committed": 5,
    "alloy_adopted": 6,
    "timers_adopted": 7,
    "systemd_adopted": 8,
    "desktop_adopted": 9,
    "completed": 10,
}


class StateError(ValueError):
    """Release state or journal violates an invariant."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _die(message: str) -> NoReturn:
    raise StateError(message)


def _safe_absolute(path: Path, *, field: str, must_exist: bool = True) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        _die(f"{field} must be an absolute canonical path without '..'")
    if must_exist and not path.exists():
        _die(f"{field} does not exist: {path}")
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise StateError(f"cannot resolve {field}: {path}") from exc
    if must_exist and stat.S_ISLNK(path.lstat().st_mode):
        _die(f"{field} must not be a symlink: {path}")
    if path != resolved:
        _die(f"{field} must already be canonical: {path} != {resolved}")
    return resolved


def _regular_file(
    path: Path,
    *,
    field: str,
    mode: int | tuple[int, ...] | None = 0o600,
) -> Path:
    resolved = _safe_absolute(path, field=field)
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode):
        _die(f"{field} must be a regular file")
    actual_mode = stat.S_IMODE(file_stat.st_mode)
    if isinstance(mode, tuple):
        if actual_mode not in mode:
            expected = " or ".join(f"{candidate:o}" for candidate in mode)
            _die(f"{field} must have mode {expected}")
    elif mode is not None and actual_mode != mode:
        _die(f"{field} must have mode {mode:o}")
    return resolved


def _directory(path: Path, *, field: str) -> Path:
    resolved = _safe_absolute(path, field=field)
    if not stat.S_ISDIR(path.lstat().st_mode):
        _die(f"{field} must be a directory")
    return resolved


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = ""
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _atomic_json(path: Path, document: dict[str, Any], *, mode: int = 0o600) -> None:
    rendered = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    _atomic_bytes(path, rendered.encode(), mode=mode)


def _atomic_symlink(path: Path, target: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    os.symlink(target, temporary)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path, *, schema: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict) or value.get("schema") != schema:
        _die(f"unexpected schema in {path}")
    return value


def _dotenv_value(content: bytes, key: str) -> str:
    prefix = f"{key}="
    matches = [
        line[len(prefix) :]
        for line in content.decode("utf-8").splitlines()
        if line.startswith(prefix)
    ]
    if len(matches) != 1 or not matches[0]:
        _die(f"release manifest must define exactly one non-empty {key}")
    return matches[0]


def _release_path_is_excluded(relative: Path, *, is_dir: bool) -> bool:
    if not relative.parts:
        return False
    if relative.parts[0] in RELEASE_EXCLUDED_TOP_LEVEL:
        return True
    if is_dir and any(part in RELEASE_EXCLUDED_ANY_DIR for part in relative.parts):
        return True
    return relative.suffix in {".pyc", ".tsbuildinfo"}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bytes_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _release_manifest_document(release_dir: Path) -> dict[str, Any]:
    root = _directory(release_dir, field="release manifest root")
    entries: list[dict[str, Any]] = []

    def visit(directory: Path) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise StateError(f"cannot enumerate release tree: {directory}") from exc
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root)
            file_stat = path.lstat()
            is_directory = stat.S_ISDIR(file_stat.st_mode)
            if _release_path_is_excluded(relative, is_dir=is_directory):
                continue
            common = {
                "path": relative.as_posix(),
                "mode": f"{stat.S_IMODE(file_stat.st_mode):04o}",
            }
            if is_directory:
                entries.append({**common, "type": "directory"})
                visit(path)
            elif stat.S_ISREG(file_stat.st_mode):
                entries.append({**common, "type": "file", "sha256": _file_sha256(path)})
            elif stat.S_ISLNK(file_stat.st_mode):
                target = os.readlink(path)
                if os.path.isabs(target):
                    _die(f"release symlink target must be relative: {relative}")
                resolved = (path.parent / target).resolve(strict=True)
                _assert_beneath(resolved, root, field=f"release symlink {relative}")
                entries.append({**common, "type": "symlink", "target": target})
            else:
                _die(f"unsupported release filesystem object: {relative}")

    visit(root)
    return {"schema": RELEASE_MANIFEST_SCHEMA, "entries": entries}


def _release_manifest_path(release_dir: Path) -> Path:
    return release_dir / RELEASE_MANIFEST_NAME


def _read_release_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    manifest_path = _regular_file(
        path,
        field="release source manifest",
        mode=(0o400, 0o600),
    )
    content = manifest_path.read_bytes()
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError(f"invalid release source manifest: {manifest_path}") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema") != RELEASE_MANIFEST_SCHEMA
        or not isinstance(document.get("entries"), list)
    ):
        _die(f"unexpected release source manifest schema: {manifest_path}")
    return document, content


def _assert_release_tree_read_only(root: Path) -> None:
    expected_uid = os.geteuid()
    expected_gid = os.getegid()
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in [".", *directory_names, *file_names]:
            path = directory_path if name == "." else directory_path / name
            metadata = path.lstat()
            relative = path.relative_to(root)
            display = "." if not relative.parts else relative.as_posix()
            if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
                _die(f"release tree is not launcher-owned: {display}")
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if stat.S_IMODE(metadata.st_mode) & 0o222:
                _die(f"release tree is writable: {display}")


def verify_release_manifest(
    release_dir: Path,
    manifest_path: Path | None = None,
    *,
    require_read_only: bool = False,
) -> bytes:
    root = _directory(release_dir, field="release directory")
    path = _release_manifest_path(root) if manifest_path is None else manifest_path
    expected, content = _read_release_manifest(path)
    actual = _release_manifest_document(root)
    expected_entries = expected.get("entries")
    actual_entries = actual.get("entries")
    if not isinstance(expected_entries, list) or not isinstance(actual_entries, list):
        _die("release tree differs from its immutable source manifest")
    if len(expected_entries) != len(actual_entries):
        _die("release tree differs from its immutable source manifest")
    for expected_entry, actual_entry in zip(
        expected_entries,
        actual_entries,
        strict=True,
    ):
        expected_without_mode = {
            key: value for key, value in expected_entry.items() if key != "mode"
        }
        actual_without_mode = {key: value for key, value in actual_entry.items() if key != "mode"}
        try:
            expected_mode = int(str(expected_entry["mode"]), 8)
            actual_mode = int(str(actual_entry["mode"]), 8)
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("release tree differs from its immutable source manifest") from exc
        if expected_without_mode != actual_without_mode or actual_mode not in {
            expected_mode,
            expected_mode & ~0o222,
        }:
            _die("release tree differs from its immutable source manifest")
    if require_read_only:
        _assert_release_tree_read_only(root)
    if expected.get("schema") != actual.get("schema"):
        _die("release tree differs from its immutable source manifest")
    return content


def write_release_manifest(args: argparse.Namespace) -> None:
    root = _directory(args.release_dir, field="release directory")
    document = _release_manifest_document(root)
    rendered = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    output = args.manifest
    if output.exists() or output.is_symlink():
        _die(f"release source manifest already exists: {output}")
    _atomic_bytes(output, rendered.encode())
    print(hashlib.sha256(rendered.encode()).hexdigest())


def verify_release_manifest_command(args: argparse.Namespace) -> None:
    content = verify_release_manifest(
        args.release_dir,
        args.manifest,
        require_read_only=args.require_read_only,
    )
    print(hashlib.sha256(content).hexdigest())


def _state_digest(
    *,
    release_id: str,
    color: str,
    release_dir: Path,
    state_metadata: bytes,
    app_env: bytes,
    release_env: bytes,
    source_manifest: bytes,
) -> str:
    digest = hashlib.sha256()
    for label, value in (
        (b"release_id", release_id.encode()),
        (b"color", color.encode()),
        (b"release_dir", os.fsencode(release_dir)),
        (b"state_metadata", state_metadata),
        (b"app_env", app_env),
        (b"release_env", release_env),
        (b"source_manifest", source_manifest),
    ):
        digest.update(label + b"\0" + value + b"\0")
    return digest.hexdigest()


def _desktop_state_digest(
    *,
    release_id: str,
    release_dir: Path,
    app_env: bytes,
    release_env: bytes,
    source_manifest: bytes,
) -> str:
    digest = hashlib.sha256()
    for label, value in (
        (b"schema", DESKTOP_STATE_DIGEST_SCHEMA.encode()),
        (b"release_id", release_id.encode()),
        (b"release_dir", os.fsencode(release_dir)),
        (b"app_env", app_env),
        (b"release_env", release_env),
        (b"source_manifest", source_manifest),
    ):
        digest.update(label + b"\0" + value + b"\0")
    return digest.hexdigest()


def _state_root(path: Path) -> Path:
    root = _directory(path, field="state root")
    if stat.S_IMODE(root.stat().st_mode) & 0o022:
        _die("state root must not be group/world writable")
    return root


def _states_root(root: Path, *, create: bool = True) -> Path:
    path = root / "active-states"
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    elif not path.exists():
        _die("active-states directory is missing")
    if path.is_symlink() or not stat.S_ISDIR(path.lstat().st_mode):
        _die("active-states must be a real directory")
    if create:
        os.chmod(path, 0o700)
    return path


def _assert_beneath(path: Path, parent: Path, *, field: str) -> None:
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise StateError(f"{field} escapes {parent}") from exc


def validate_state(path: Path, *, root: Path) -> dict[str, Any]:
    states = _states_root(root, create=False).resolve(strict=True)
    state_dir = _directory(path, field="release state")
    _assert_beneath(state_dir, states, field="release state")
    if state_dir.parent != states:
        _die("release state must be one direct immutable child of active-states")

    metadata_path = _regular_file(state_dir / "state.json", field="state metadata")
    app_path = _regular_file(state_dir / "app.env", field="state app env")
    manifest_path = _regular_file(state_dir / "release-images.env", field="state release manifest")
    color_path = _regular_file(state_dir / "color", field="state color")
    metadata = _read_json(metadata_path, schema=SCHEMA)
    if set(metadata) != STATE_METADATA_FIELDS:
        _die("release state metadata fields are invalid")
    if metadata_path.read_bytes() != _canonical_json_bytes(metadata):
        _die("release state metadata is not canonical")
    color = color_path.read_text(encoding="ascii").strip()
    release_id = metadata.get("release_id")
    if color not in ALLOWED_COLORS or metadata.get("color") != color:
        _die("release state color mismatch")
    if not isinstance(release_id, str) or not SAFE_ID.fullmatch(release_id):
        _die("invalid release state id")
    prepared_at = metadata.get("prepared_at")
    if not isinstance(prepared_at, str):
        _die("release state prepared_at is invalid")
    try:
        prepared_datetime = datetime.fromisoformat(prepared_at)
    except ValueError as exc:
        raise StateError("release state prepared_at is invalid") from exc
    if prepared_datetime.tzinfo is None or prepared_datetime.utcoffset() != timezone.utc.utcoffset(
        None
    ):
        _die("release state prepared_at must be UTC")

    release_link = state_dir / "release"
    if not release_link.is_symlink():
        _die("release state must contain a release symlink")
    release_dir = release_link.resolve(strict=True)
    declared_release_dir_value = metadata.get("release_dir")
    release_root_value = metadata.get("release_root")
    if not isinstance(declared_release_dir_value, str) or not isinstance(
        release_root_value,
        str,
    ):
        _die("release state path metadata is invalid")
    declared_release_dir = Path(declared_release_dir_value)
    release_root = Path(release_root_value)
    _directory(release_dir, field="state release directory")
    _directory(release_root, field="state release root")
    _assert_beneath(release_dir, release_root, field="state release directory")
    if release_dir.parent != release_root or release_dir != declared_release_dir:
        _die("state release directory identity mismatch")

    app_content = app_path.read_bytes()
    manifest_content = manifest_path.read_bytes()
    _, source_manifest = _read_release_manifest(_release_manifest_path(release_dir))
    if _dotenv_value(manifest_content, "RELEASE_ID") != release_id:
        _die("state manifest RELEASE_ID mismatch")
    state_metadata = {key: metadata[key] for key in STATE_METADATA_BOUND_FIELDS}
    expected_digest = _state_digest(
        release_id=release_id,
        color=color,
        release_dir=release_dir,
        state_metadata=_canonical_json_bytes(state_metadata),
        app_env=app_content,
        release_env=manifest_content,
        source_manifest=source_manifest,
    )
    if metadata.get("content_digest") != expected_digest:
        _die("release state content digest mismatch")
    effective_config_fingerprint = _bytes_sha256(app_content)
    if metadata.get("effective_config_fingerprint") != effective_config_fingerprint:
        _die("release state effective config fingerprint mismatch")
    expected_state_id = f"{release_id}-{color}-{expected_digest[:16]}"
    if metadata.get("state_id") != expected_state_id or state_dir.name != expected_state_id:
        _die("release state directory does not match its content digest")
    return {
        **metadata,
        "state_dir": str(state_dir),
        "app_env": str(app_path),
        "release_env": str(manifest_path),
        "release_dir": str(release_dir),
    }


def _require_runtime_owned(
    path: Path,
    *,
    field: str,
    mode: int,
    kind: str,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise StateError(f"{field} is missing") from exc
    kind_ok = (
        stat.S_ISDIR(metadata.st_mode) if kind == "directory" else stat.S_ISREG(metadata.st_mode)
    )
    if (
        not kind_ok
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != mode
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
    ):
        _die(f"{field} must be launcher-owned {kind} mode {mode:04o}")


def _require_runtime_symlink(path: Path, *, field: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise StateError(f"{field} is missing") from exc
    if (
        not stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
    ):
        _die(f"{field} must be a launcher-owned symlink")


def _desktop_digest_inputs(
    *,
    release_dir: Path,
    app_env: Path,
    release_env: Path,
) -> tuple[str, bytes, bytes, bytes]:
    canonical_release = _directory(
        release_dir,
        field="desktop release directory",
    )
    app_content = _regular_file(
        app_env,
        field="desktop state app env",
        mode=(0o400, 0o600),
    ).read_bytes()
    release_content = _regular_file(
        release_env,
        field="desktop state release manifest",
        mode=(0o400, 0o600),
    ).read_bytes()
    release_id = _dotenv_value(release_content, "RELEASE_ID")
    if not SAFE_ID.fullmatch(release_id):
        _die("desktop state release id is invalid")
    for key in (
        "BROWSER_AGENT_IMAGE",
        "DESKTOP_WEBTOP_IMAGE",
    ):
        if not IMMUTABLE_IMAGE.fullmatch(_dotenv_value(release_content, key)):
            _die(f"desktop state {key} is not digest-pinned")
    _, source_manifest = _read_release_manifest(_release_manifest_path(canonical_release))
    return release_id, app_content, release_content, source_manifest


def desktop_digest(args: argparse.Namespace) -> None:
    release_dir = _directory(
        args.release_dir,
        field="desktop release directory",
    )
    release_id, app_content, release_content, source_manifest = _desktop_digest_inputs(
        release_dir=release_dir,
        app_env=args.app_env,
        release_env=args.release_env,
    )
    print(
        _desktop_state_digest(
            release_id=release_id,
            release_dir=release_dir,
            app_env=app_content,
            release_env=release_content,
            source_manifest=source_manifest,
        )
    )


def validate_desktop_state(path: Path, *, root: Path) -> dict[str, Any]:
    states = _directory(
        root / "desktop-states",
        field="desktop states root",
    )
    _require_runtime_owned(
        states,
        field="desktop states root",
        mode=0o700,
        kind="directory",
    )
    state_dir = _directory(path, field="desktop state")
    _assert_beneath(state_dir, states, field="desktop state")
    if state_dir.parent != states or not DESKTOP_SAFE_ID.fullmatch(state_dir.name):
        _die("desktop state must be one direct immutable child of desktop-states")
    _require_runtime_owned(
        state_dir,
        field="desktop state",
        mode=0o700,
        kind="directory",
    )

    app_path = state_dir / "app.env"
    release_env_path = state_dir / "release-images.env"
    fingerprint_path = state_dir / "fingerprint"
    for file_path, label in (
        (app_path, "desktop state app env"),
        (release_env_path, "desktop state release manifest"),
        (fingerprint_path, "desktop state fingerprint"),
    ):
        _require_runtime_owned(
            file_path,
            field=label,
            mode=0o600,
            kind="file",
        )

    release_link = state_dir / "release"
    _require_runtime_symlink(
        release_link,
        field="desktop state release pointer",
    )
    try:
        release_dir = release_link.resolve(strict=True)
    except OSError as exc:
        raise StateError("desktop state release pointer does not resolve") from exc
    release_root = _directory(
        root.parent / "releases",
        field="release root",
    )
    _directory(release_dir, field="desktop state release directory")
    _assert_beneath(
        release_dir,
        release_root,
        field="desktop state release directory",
    )
    if release_dir.parent != release_root:
        _die("desktop state release must be one direct child of release root")

    release_id, app_content, release_content, source_manifest = _desktop_digest_inputs(
        release_dir=release_dir,
        app_env=app_path,
        release_env=release_env_path,
    )
    try:
        fingerprint_content = fingerprint_path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise StateError("desktop state fingerprint is unreadable") from exc
    if not re.fullmatch(r"[0-9a-f]{64}\n?", fingerprint_content):
        _die("desktop state fingerprint is invalid")
    fingerprint = fingerprint_content.rstrip("\n")
    expected = _desktop_state_digest(
        release_id=release_id,
        release_dir=release_dir,
        app_env=app_content,
        release_env=release_content,
        source_manifest=source_manifest,
    )
    if fingerprint != expected:
        _die("desktop state content digest mismatch")
    if state_dir.name != f"{release_id}-{fingerprint[:16]}":
        _die("desktop state directory does not match its content digest")
    return {
        "schema": RUNTIME_STATE_SCHEMA,
        "state": "desktop",
        "state_dir": str(state_dir),
        "release_dir": str(release_dir),
        "release_id": release_id,
        "content_digest": fingerprint,
    }


def verify_desktop_state_command(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    document = validate_desktop_state(args.state_dir, root=root)
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))


def verify_runtime_state(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    if args.state == "app":
        states = _states_root(root, create=False)
        _require_runtime_owned(
            states,
            field="application states root",
            mode=0o700,
            kind="directory",
        )
        active_pointer = root / "active-state"
        _require_runtime_symlink(
            active_pointer,
            field="active application state pointer",
        )
        document = active_state(root)
        state_dir = Path(document["state_dir"])
        _require_runtime_owned(
            state_dir,
            field="active application state",
            mode=0o700,
            kind="directory",
        )
        for name, label in (
            ("state.json", "application state metadata"),
            ("app.env", "application state app env"),
            ("release-images.env", "application state release manifest"),
            ("color", "application state color"),
        ):
            _require_runtime_owned(
                state_dir / name,
                field=label,
                mode=0o600,
                kind="file",
            )
        _require_runtime_symlink(
            state_dir / "release",
            field="application state release pointer",
        )
        result = {
            "schema": RUNTIME_STATE_SCHEMA,
            "state": "app",
            "state_dir": str(state_dir),
            "release_dir": document["release_dir"],
            "release_id": document["release_id"],
            "content_digest": document["content_digest"],
        }
    else:
        active_pointer = root / "active-desktop-state"
        _require_runtime_symlink(
            active_pointer,
            field="active desktop state pointer",
        )
        try:
            active_desktop_state = active_pointer.resolve(strict=True)
        except OSError as exc:
            raise StateError("active desktop state pointer does not resolve") from exc
        result = validate_desktop_state(
            active_desktop_state,
            root=root,
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


def active_state(root: Path, *, required: bool = True) -> dict[str, Any] | None:
    pointer = root / "active-state"
    if not pointer.exists() and not pointer.is_symlink():
        if required:
            _die("active-state pointer is missing")
        return None
    if not pointer.is_symlink():
        _die("active-state must be a symlink")
    return validate_state(pointer.resolve(strict=True), root=root)


def prepare_state(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    release_root = _directory(args.release_root, field="release root")
    release_dir = _directory(args.release_dir, field="release directory")
    _assert_beneath(release_dir, release_root, field="release directory")
    if release_dir.parent != release_root:
        _die("release directory must be one direct child of release root")
    app_path = _regular_file(
        args.app_env,
        field="candidate app env",
        mode=(0o400, 0o600),
    )
    if args.color not in ALLOWED_COLORS:
        _die("invalid color")
    if not SAFE_ID.fullmatch(args.release_id):
        _die("invalid release id")

    app_content = app_path.read_bytes()
    if args.release_env is None:
        _die("release manifest is required")
    manifest_content = _regular_file(
        args.release_env,
        field="candidate release manifest",
        mode=(0o400, 0o600),
    ).read_bytes()
    if _dotenv_value(manifest_content, "RELEASE_ID") != args.release_id:
        _die("candidate manifest RELEASE_ID mismatch")

    source_manifest = verify_release_manifest(
        release_dir,
        require_read_only=True,
    )
    effective_config_fingerprint = _bytes_sha256(app_content)
    prepared_at = _utc_now()
    state_metadata = {
        "schema": SCHEMA,
        "release_id": args.release_id,
        "color": args.color,
        "release_root": str(release_root),
        "release_dir": str(release_dir),
        "effective_config_fingerprint": effective_config_fingerprint,
        "prepared_at": prepared_at,
    }
    content_digest = _state_digest(
        release_id=args.release_id,
        color=args.color,
        release_dir=release_dir,
        state_metadata=_canonical_json_bytes(state_metadata),
        app_env=app_content,
        release_env=manifest_content,
        source_manifest=source_manifest,
    )
    state_id = f"{args.release_id}-{args.color}-{content_digest[:16]}"
    if len(state_id) > 128 or not SAFE_ID.fullmatch(state_id):
        _die("derived state id is invalid")
    states = _states_root(root)
    destination = states / state_id
    if destination.exists():
        validate_state(destination, root=root)
        print(destination)
        return

    temporary: Path | None = Path(tempfile.mkdtemp(prefix=".prepare-", dir=states))
    os.chmod(temporary, 0o700)
    try:
        _atomic_bytes(temporary / "app.env", app_content)
        _atomic_bytes(temporary / "release-images.env", manifest_content)
        _atomic_bytes(temporary / "color", f"{args.color}\n".encode())
        os.symlink(release_dir, temporary / "release")
        metadata = {
            **state_metadata,
            "state_id": state_id,
            "content_digest": content_digest,
        }
        _atomic_json(temporary / "state.json", metadata)
        _fsync_directory(temporary)
        os.replace(temporary, destination)
        _fsync_directory(states)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
    validate_state(destination, root=root)
    print(destination)


def _journal_path(root: Path) -> Path:
    return root / "release-transaction.json"


def read_journal(root: Path, *, required: bool = True) -> dict[str, Any] | None:
    path = _journal_path(root)
    if not path.exists():
        if required:
            _die("release transaction journal is missing")
        return None
    _regular_file(path, field="release transaction journal")
    document = _read_json(path, schema=JOURNAL_SCHEMA)
    if document.get("stage") not in STAGES:
        _die("invalid release transaction stage")
    candidate = validate_state(Path(str(document.get("candidate_state", ""))), root=root)
    previous_raw = document.get("previous_state")
    previous = None
    if previous_raw is not None:
        previous = validate_state(Path(str(previous_raw)), root=root)
    return {**document, "candidate": candidate, "previous": previous}


def begin_transaction(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    candidate = validate_state(args.candidate_state, root=root)
    previous = active_state(root, required=False)
    existing = read_journal(root, required=False)
    if existing is not None:
        if existing["candidate"]["state_dir"] == candidate["state_dir"]:
            print(existing["transaction_id"])
            return
        _die("another release transaction requires reconciliation")
    document = {
        "schema": JOURNAL_SCHEMA,
        "transaction_id": uuid.uuid4().hex,
        "stage": "prepared",
        "candidate_state": candidate["state_dir"],
        "previous_state": None if previous is None else previous["state_dir"],
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    _atomic_json(_journal_path(root), document)
    print(document["transaction_id"])


def update_stage(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    journal = read_journal(root)
    current = str(journal["stage"])
    if args.stage not in STAGES or STAGES[args.stage] < STAGES[current]:
        _die(f"invalid journal stage transition: {current} -> {args.stage}")
    document = {
        key: value for key, value in journal.items() if key not in {"candidate", "previous"}
    }
    document["stage"] = args.stage
    document["updated_at"] = _utc_now()
    _atomic_json(_journal_path(root), document)


def arm_cutover(args: argparse.Namespace) -> None:
    """Persist the one absolute deadline for traffic, workers and rollback.

    Preparation and migrations intentionally happen before this clock starts.
    Once public routing can change, every forward and reconciliation step must
    consume the same wall-clock budget, including recovery after a process
    crash.  Repeating the command with the same value is idempotent; moving the
    deadline is forbidden.
    """

    root = _state_root(args.state_root)
    journal = read_journal(root)
    deadline_epoch = args.deadline_epoch
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    if deadline_epoch <= now_epoch or deadline_epoch > now_epoch + 180:
        _die("cutover deadline must be within the next 180 seconds")
    existing = journal.get("cutover_deadline_epoch")
    if existing is not None and existing != deadline_epoch:
        _die("cutover deadline is immutable once armed")
    document = {
        key: value for key, value in journal.items() if key not in {"candidate", "previous"}
    }
    document["cutover_deadline_epoch"] = deadline_epoch
    document["updated_at"] = _utc_now()
    _atomic_json(_journal_path(root), document)


def select_initial_candidate(args: argparse.Namespace) -> None:
    """Make the first clean installation forward-only before external cutover.

    A fresh installation has no runtime to roll back to. The recovery policy is
    fsynced before the active pointer moves, so a crash between those writes
    always converges to the already-healthy first blue candidate.
    """

    root = _state_root(args.state_root)
    journal = read_journal(root)
    candidate = journal["candidate"]
    previous = journal["previous"]
    if previous is not None:
        _die("initial selection requires no previous release")
    if candidate["color"] != "blue":
        _die("initial selection requires a blue candidate")
    stage = str(journal["stage"])
    if STAGES[stage] < STAGES["candidate_started"] or STAGES[stage] >= STAGES["committed"]:
        _die("forward-only selection requires a healthy uncommitted candidate")
    deadline = journal.get("cutover_deadline_epoch")
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    if not isinstance(deadline, int) or deadline <= now_epoch:
        _die("forward-only selection requires a live immutable cutover deadline")

    current = active_state(root, required=False)
    if current is not None and current["state_dir"] != candidate["state_dir"]:
        _die("active-state appeared after initial installation began")
    policy = journal.get("recovery_policy")
    if policy not in {None, "initial_forward_only"}:
        _die("release transaction has an incompatible recovery policy")
    if policy is None:
        document = {
            key: value for key, value in journal.items() if key not in {"candidate", "previous"}
        }
        document["recovery_policy"] = "initial_forward_only"
        document["forward_only_at"] = _utc_now()
        document["updated_at"] = _utc_now()
        _atomic_json(_journal_path(root), document)
    if args.failpoint == "after-policy":
        os._exit(88)
    if current is None:
        relative_target = os.path.relpath(candidate["state_dir"], root)
        _atomic_symlink(root / "active-state", relative_target)
    if args.failpoint == "after-pointer":
        os._exit(89)


def commit_transaction(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    journal = read_journal(root)
    candidate = journal["candidate"]
    previous = journal["previous"]
    current = active_state(root, required=False)
    expected_previous = None if previous is None else previous["state_dir"]
    current_path = None if current is None else current["state_dir"]
    pointer_already_committed = current_path == candidate["state_dir"]
    if current_path != expected_previous and not pointer_already_committed:
        _die("active-state changed after the transaction began")
    current_stage = str(journal["stage"])
    if STAGES[current_stage] < STAGES["accepted"]:
        _die("release cannot commit before acceptance")
    if STAGES[current_stage] > STAGES["committed"]:
        if not pointer_already_committed:
            _die("post-commit adoption state does not own active-state")
        return
    if current_stage == "committed" and pointer_already_committed:
        return
    if not pointer_already_committed:
        if args.failpoint == "before-pointer":
            os._exit(86)
        relative_target = os.path.relpath(candidate["state_dir"], root)
        _atomic_symlink(root / "active-state", relative_target)
        if args.failpoint == "after-pointer":
            os._exit(87)
    document = {
        key: value for key, value in journal.items() if key not in {"candidate", "previous"}
    }
    document["stage"] = "committed"
    document["committed_at"] = _utc_now()
    cutover_deadline = journal.get("cutover_deadline_epoch")
    if not isinstance(cutover_deadline, int):
        _die("application commit requires the immutable cutover deadline")
    document["updated_at"] = _utc_now()
    _atomic_json(_journal_path(root), document)


def rollback_committed_transaction(args: argparse.Namespace) -> None:
    """Atomically re-select N-1 after the coordinated desktop cutover fails.

    This is deliberately narrower than a general downgrade command: it is
    valid only while the journal is exactly at the application commit point,
    a previous immutable state exists, and the active pointer still selects
    the journal candidate. The desktop child has already restored and verified
    the previous browser contract under the same maintenance fence.
    """

    root = _state_root(args.state_root)
    journal = read_journal(root)
    candidate = journal["candidate"]
    previous = journal["previous"]
    if previous is None:
        _die("a first release has no previous application state to restore")
    current = active_state(root)
    current_stage = str(journal["stage"])
    if (
        current["state_dir"] == previous["state_dir"]
        and current_stage == "accepted"
        and journal.get("rollback_selected_at")
    ):
        return
    if current_stage != "committed":
        _die("application rollback is allowed only at the exact commit stage")
    rollback_from_release_id = journal.get("rollback_from_release_id")
    if rollback_from_release_id is not None and rollback_from_release_id != candidate["release_id"]:
        _die("application rollback intent belongs to another candidate")
    if not journal.get("rollback_requested_at"):
        rollback_requested_at = _utc_now()
        intent = {
            key: value for key, value in journal.items() if key not in {"candidate", "previous"}
        }
        intent["rollback_requested_at"] = rollback_requested_at
        intent["rollback_from_release_id"] = candidate["release_id"]
        intent["updated_at"] = rollback_requested_at
        _atomic_json(_journal_path(root), intent)
        journal = {
            **journal,
            "rollback_requested_at": rollback_requested_at,
            "rollback_from_release_id": candidate["release_id"],
            "updated_at": rollback_requested_at,
        }
    if current["state_dir"] == candidate["state_dir"]:
        if args.failpoint == "before-pointer":
            os._exit(90)
        relative_target = os.path.relpath(previous["state_dir"], root)
        _atomic_symlink(root / "active-state", relative_target)
        if args.failpoint == "after-pointer":
            os._exit(91)
    elif current["state_dir"] != previous["state_dir"]:
        _die("active-state no longer selects the candidate or its previous state")
    document = {
        key: value for key, value in journal.items() if key not in {"candidate", "previous"}
    }
    document["stage"] = "accepted"
    document["rollback_selected_at"] = _utc_now()
    document["rollback_from_release_id"] = candidate["release_id"]
    document["updated_at"] = _utc_now()
    _atomic_json(_journal_path(root), document)


def ensure_runtime_links(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    root_dir = _directory(args.root_dir, field="application root")
    active_state(root)
    for path, target in (
        (root / "active-app.env", "active-state/app.env"),
        (root / "active-release-images.env", "active-state/release-images.env"),
        (root / "active-color", "active-state/color"),
        (root_dir / "current", "shared/active-state/release"),
    ):
        if path.exists() and not path.is_symlink():
            _die(f"compatibility runtime path is not a symlink: {path}")
        if path.is_symlink() and os.readlink(path) == target:
            continue
        _atomic_symlink(path, target)


def complete_transaction(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    journal = read_journal(root)
    active = active_state(root)
    if active["state_dir"] != journal["candidate"]["state_dir"]:
        _die("cannot complete a transaction whose candidate is not active")
    if journal["stage"] != "desktop_adopted":
        _die("release adoption must finish before transaction completion")
    document = {
        key: value for key, value in journal.items() if key not in {"candidate", "previous"}
    }
    document["stage"] = "completed"
    document["completed_at"] = _utc_now()
    document["updated_at"] = _utc_now()
    _atomic_json(_journal_path(root), document)
    history = root / "release-transactions"
    history.mkdir(mode=0o700, exist_ok=True)
    os.chmod(history, 0o700)
    destination = history / f"{journal['transaction_id']}.json"
    if destination.exists():
        _die("release transaction history collision")
    os.replace(_journal_path(root), destination)
    _fsync_directory(history)
    _fsync_directory(root)


def abort_transaction(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    journal = read_journal(root)
    active = active_state(root, required=False)
    previous = journal["previous"]
    active_path = None if active is None else active["state_dir"]
    previous_path = None if previous is None else previous["state_dir"]
    if active_path != previous_path:
        _die("cannot abort after active-state moved away from the previous state")
    if STAGES[str(journal["stage"])] >= STAGES["committed"]:
        _die("a committed transaction cannot be aborted")
    document = {
        key: value for key, value in journal.items() if key not in {"candidate", "previous"}
    }
    document["stage"] = "aborted"
    document["outcome"] = "rolled_back"
    document["completed_at"] = _utc_now()
    document["updated_at"] = _utc_now()
    _atomic_json(_journal_path(root), document)
    history = root / "release-transactions"
    history.mkdir(mode=0o700, exist_ok=True)
    os.chmod(history, 0o700)
    destination = history / f"{journal['transaction_id']}.json"
    if destination.exists():
        _die("release transaction history collision")
    os.replace(_journal_path(root), destination)
    _fsync_directory(history)
    _fsync_directory(root)


def get_field(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    if args.source == "active":
        document = active_state(root)
    else:
        journal = read_journal(root)
        document = journal
        if args.source in {"candidate", "previous"}:
            document = journal[args.source]
            if document is None:
                raise SystemExit(2)
    value = document.get(args.field)
    if value is None:
        raise SystemExit(2)
    if isinstance(value, (dict, list)):
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    else:
        print(value)


def mark_rollback_failed(args: argparse.Namespace) -> None:
    root = _state_root(args.state_root)
    journal = read_journal(root, required=False)
    transaction_id = "unknown" if journal is None else str(journal["transaction_id"])
    failures = [item[:160] for item in args.failure[:20]]
    marker = {
        "schema": ROLLBACK_MARKER_SCHEMA,
        "transaction_id": transaction_id,
        "failed_at": _utc_now(),
        "failures": failures,
    }
    _atomic_json(root / "rollback-failed.json", marker)
    # The transaction marker is immutable forensic evidence. Active alert state
    # is emitted by the shared root-owned writer and is cleared only by a later
    # successful reconciliation, which records an explicit recovery.
    legacy_metric = root / "metrics" / "release-rollback.prom"
    if legacy_metric.exists() or legacy_metric.is_symlink():
        legacy_metric.unlink()
    try:
        from host_metrics import record_host_operation

        record_host_operation("release_rollback", "failure")
    except (ImportError, OSError, RuntimeError) as exc:
        print(
            f"CRITICAL: rollback marker persisted but host metric write failed: {exc}",
            file=sys.stderr,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_write = subparsers.add_parser("manifest-write")
    manifest_write.add_argument("--release-dir", type=Path, required=True)
    manifest_write.add_argument("--manifest", type=Path, required=True)
    manifest_write.set_defaults(func=write_release_manifest)

    manifest_verify = subparsers.add_parser("manifest-verify")
    manifest_verify.add_argument("--release-dir", type=Path, required=True)
    manifest_verify.add_argument("--manifest", type=Path, required=True)
    manifest_verify.add_argument("--require-read-only", action="store_true")
    manifest_verify.set_defaults(func=verify_release_manifest_command)

    desktop_fingerprint = subparsers.add_parser("desktop-digest")
    desktop_fingerprint.add_argument("--release-dir", type=Path, required=True)
    desktop_fingerprint.add_argument("--app-env", type=Path, required=True)
    desktop_fingerprint.add_argument("--release-env", type=Path, required=True)
    desktop_fingerprint.set_defaults(func=desktop_digest)

    desktop_verify = subparsers.add_parser("desktop-verify")
    desktop_verify.add_argument("--state-root", type=Path, required=True)
    desktop_verify.add_argument("--state-dir", type=Path, required=True)
    desktop_verify.set_defaults(func=verify_desktop_state_command)

    runtime_verify = subparsers.add_parser("runtime-verify")
    runtime_verify.add_argument("--state-root", type=Path, required=True)
    runtime_verify.add_argument("--state", choices=("app", "desktop"), required=True)
    runtime_verify.set_defaults(func=verify_runtime_state)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--state-root", type=Path, required=True)
    prepare.add_argument("--release-root", type=Path, required=True)
    prepare.add_argument("--release-dir", type=Path, required=True)
    prepare.add_argument("--app-env", type=Path, required=True)
    prepare.add_argument("--release-env", type=Path)
    prepare.add_argument("--release-id", required=True)
    prepare.add_argument("--color", choices=sorted(ALLOWED_COLORS), required=True)
    prepare.set_defaults(func=prepare_state)

    begin = subparsers.add_parser("begin")
    begin.add_argument("--state-root", type=Path, required=True)
    begin.add_argument("--candidate-state", type=Path, required=True)
    begin.set_defaults(func=begin_transaction)

    stage = subparsers.add_parser("stage")
    stage.add_argument("--state-root", type=Path, required=True)
    stage.add_argument("--stage", choices=sorted(STAGES), required=True)
    stage.set_defaults(func=update_stage)

    cutover = subparsers.add_parser("arm-cutover")
    cutover.add_argument("--state-root", type=Path, required=True)
    cutover.add_argument("--deadline-epoch", type=int, required=True)
    cutover.set_defaults(func=arm_cutover)

    initial = subparsers.add_parser("select-initial")
    initial.add_argument("--state-root", type=Path, required=True)
    initial.add_argument("--failpoint", choices=("after-policy", "after-pointer"))
    initial.set_defaults(func=select_initial_candidate)

    commit = subparsers.add_parser("commit")
    commit.add_argument("--state-root", type=Path, required=True)
    commit.add_argument("--failpoint", choices=("before-pointer", "after-pointer"))
    commit.set_defaults(func=commit_transaction)

    rollback_commit = subparsers.add_parser("rollback-commit")
    rollback_commit.add_argument("--state-root", type=Path, required=True)
    rollback_commit.add_argument("--failpoint", choices=("before-pointer", "after-pointer"))
    rollback_commit.set_defaults(func=rollback_committed_transaction)

    complete = subparsers.add_parser("complete")
    complete.add_argument("--state-root", type=Path, required=True)
    complete.set_defaults(func=complete_transaction)

    abort = subparsers.add_parser("abort")
    abort.add_argument("--state-root", type=Path, required=True)
    abort.set_defaults(func=abort_transaction)

    links = subparsers.add_parser("ensure-links")
    links.add_argument("--state-root", type=Path, required=True)
    links.add_argument("--root-dir", type=Path, required=True)
    links.set_defaults(func=ensure_runtime_links)

    get = subparsers.add_parser("get")
    get.add_argument("--state-root", type=Path, required=True)
    get.add_argument("--source", choices=("active", "journal", "candidate", "previous"))
    get.add_argument("--field", required=True)
    get.set_defaults(func=get_field)

    marker = subparsers.add_parser("mark-rollback-failed")
    marker.add_argument("--state-root", type=Path, required=True)
    marker.add_argument("--failure", action="append", default=[])
    marker.set_defaults(func=mark_rollback_failed)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except StateError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
