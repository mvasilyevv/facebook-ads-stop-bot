#!/usr/bin/env python3
"""Verify one committed immutable release, then execute its exact entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import NoReturn

VERIFIER_SCHEMA = "fb-agent-verified-release-exec/v1"
STATE_CONTRACTS = {
    "app": ("active-state", "active-states"),
    "desktop": ("active-desktop-state", "desktop-states"),
}


class VerificationError(RuntimeError):
    """The stable launcher or selected release violates its trust contract."""


def _fail(message: str) -> NoReturn:
    raise VerificationError(message)


def _safe_root(value: str) -> Path:
    root = Path(value)
    if not root.is_absolute() or ".." in root.parts:
        _fail("FB_AGENT_ROOT must be a safe absolute path")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("FB_AGENT_ROOT does not exist") from exc
    if root != resolved or not stat.S_ISDIR(root.lstat().st_mode):
        _fail("FB_AGENT_ROOT must be a canonical directory")
    metadata = root.stat()
    if (
        metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        _fail("FB_AGENT_ROOT must be owned by the launcher uid/gid and not group/world writable")
    return resolved


def _require_stable_runtime() -> Path:
    executable = Path(__file__).resolve(strict=True)
    runtime_dir = executable.parent
    state_verifier = runtime_dir / "release-state.py"
    for path, expected_mode in (
        (runtime_dir, 0o555),
        (executable, 0o555),
        (state_verifier, 0o444),
    ):
        metadata = path.stat()
        if (
            metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != expected_mode
        ):
            _fail(
                f"stable verifier runtime must be launcher-owned mode {expected_mode:04o}: {path}"
            )
    if not stat.S_ISREG(executable.stat().st_mode):
        _fail("stable release launcher is not a regular file")
    if not stat.S_ISREG(state_verifier.stat().st_mode):
        _fail("stable release-state verifier is not a regular file")
    return state_verifier


def _direct_child(path: Path, parent: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise VerificationError(f"{label} does not resolve") from exc
    if resolved.parent != resolved_parent:
        _fail(f"{label} is outside {resolved_parent}")
    return resolved


def _require_root_controlled_directory(path: Path, *, label: str) -> None:
    metadata = path.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        _fail(f"{label} must be a launcher-owned protected directory")


def _active_release(root: Path, state: str) -> tuple[Path, Path]:
    active_name, states_name = STATE_CONTRACTS[state]
    shared = root / "shared"
    releases = root / "releases"
    active_link = shared / active_name
    states_dir = shared / states_name
    try:
        _require_root_controlled_directory(shared, label="shared state")
        _require_root_controlled_directory(states_dir, label=f"{state} states")
        _require_root_controlled_directory(releases, label="release root")
    except OSError as exc:
        raise VerificationError(f"{state} release state is missing or inaccessible") from exc
    if not active_link.is_symlink():
        _fail(f"{active_name} must be a symlink")
    active_metadata = active_link.lstat()
    if active_metadata.st_uid != os.geteuid() or active_metadata.st_gid != os.getegid():
        _fail(f"{active_name} must be launcher-owned")
    state_dir = _direct_child(
        active_link,
        states_dir,
        label=f"{state} active state",
    )
    _require_root_controlled_directory(state_dir, label=f"{state} active state")
    release_link = state_dir / "release"
    if not release_link.is_symlink():
        _fail(f"{state} active state has no release symlink")
    release_metadata = release_link.lstat()
    if release_metadata.st_uid != os.geteuid() or release_metadata.st_gid != os.getegid():
        _fail(f"{state} release pointer must be launcher-owned")
    release_dir = _direct_child(
        release_link,
        releases,
        label=f"{state} active release",
    )
    _require_root_controlled_directory(
        release_dir,
        label=f"{state} active release",
    )
    return state_dir, release_dir


def _verify_release(state_verifier: Path, release_dir: Path) -> None:
    manifest = release_dir / ".fb-agent-source-manifest.json"
    if manifest.is_symlink() or not manifest.is_file():
        _fail("active release has no regular immutable source manifest")
    result = subprocess.run(
        [
            sys.executable,
            str(state_verifier),
            "manifest-verify",
            "--release-dir",
            str(release_dir),
            "--manifest",
            str(manifest),
            "--require-read-only",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        _fail(f"active release manifest verification failed{suffix}")


def _verify_state_contract(
    state_verifier: Path,
    root: Path,
    state: str,
) -> tuple[Path, Path]:
    result = subprocess.run(
        [
            sys.executable,
            str(state_verifier),
            "runtime-verify",
            "--state-root",
            str(root / "shared"),
            "--state",
            state,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        _fail(f"active {state} state verification failed{suffix}")
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"active {state} state verifier returned invalid JSON") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema") != "fb-agent-runtime-state/v1"
        or document.get("state") != state
    ):
        _fail(f"active {state} state verifier returned an invalid contract")
    state_dir_value = document.get("state_dir")
    release_dir_value = document.get("release_dir")
    if not isinstance(state_dir_value, str) or not isinstance(
        release_dir_value,
        str,
    ):
        _fail(f"active {state} state verifier returned invalid paths")
    state_path = Path(state_dir_value)
    release_path = Path(release_dir_value)
    if (
        not state_path.is_absolute()
        or ".." in state_path.parts
        or not release_path.is_absolute()
        or ".." in release_path.parts
    ):
        _fail(f"active {state} state verifier returned unsafe paths")
    try:
        state_dir = state_path.resolve(strict=True)
        release_dir = release_path.resolve(strict=True)
    except OSError as exc:
        raise VerificationError(f"active {state} state verifier returned invalid paths") from exc
    if state_path != state_dir or release_path != release_dir:
        _fail(f"active {state} state verifier returned non-canonical paths")
    return state_dir, release_dir


def _entrypoint(release_dir: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.parts[0] != "scripts"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        _fail("entrypoint must be a normalized relative path below scripts/")
    candidate = release_dir.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("verified release entrypoint does not exist") from exc
    if candidate != resolved:
        _fail("verified release entrypoint must not traverse symlinks")
    metadata = candidate.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not metadata.st_mode & 0o111
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
    ):
        _fail("verified release entrypoint must be a launcher-owned executable regular file")
    return candidate


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    try:
        separator = argv.index("--")
    except ValueError:
        separator = -1
    if separator < 0:
        raise VerificationError("exact entrypoint arguments require a -- separator")
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", choices=sorted(STATE_CONTRACTS), required=True)
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument(
        "--root",
        default=os.environ.get("FB_AGENT_ROOT", "/opt/fb-agent"),
    )
    return parser.parse_args(argv[:separator]), argv[separator + 1 :]


def main(argv: list[str] | None = None) -> int:
    try:
        args, entrypoint_args = parse_args(sys.argv[1:] if argv is None else argv)
        state_verifier = _require_stable_runtime()
        root = _safe_root(args.root)
        state_dir, release_dir = _active_release(root, args.state)
        verified_state_dir, verified_release_dir = _verify_state_contract(
            state_verifier,
            root,
            args.state,
        )
        if verified_state_dir != state_dir or verified_release_dir != release_dir:
            _fail(f"active {args.state} state changed during cryptographic verification")
        _verify_release(state_verifier, release_dir)
        app_state_dir = state_dir
        if args.state == "desktop":
            observed_app_state, observed_app_release = _active_release(
                root,
                "app",
            )
            verified_app_state, verified_app_release = _verify_state_contract(
                state_verifier,
                root,
                "app",
            )
            if (
                verified_app_state != observed_app_state
                or verified_app_release != observed_app_release
            ):
                _fail("active app state changed during desktop dependency verification")
            if observed_app_release != release_dir:
                _verify_release(state_verifier, observed_app_release)
            app_state_dir = observed_app_state
        entrypoint = _entrypoint(release_dir, args.entrypoint)
        os.environ["FB_AGENT_ROOT"] = str(root)
        os.environ["FB_AGENT_ACTIVE_STATE_DIR"] = str(state_dir)
        os.environ["FB_AGENT_APP_STATE_DIR"] = str(app_state_dir)
        os.environ["FB_AGENT_RELEASE_DIR"] = str(release_dir)
        os.environ["FB_AGENT_VERIFIED_RELEASE_SCHEMA"] = VERIFIER_SCHEMA
        os.execv(str(entrypoint), [str(entrypoint), *entrypoint_args])
    except VerificationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
