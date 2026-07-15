#!/usr/bin/env python3
"""Atomically synchronize the server-only API key into Caddy's env file.

The production shared .env remains the source of truth.  We intentionally do
not source either file in a shell: values may contain shell metacharacters and
must never be evaluated or printed.
"""

from __future__ import annotations

import argparse
import os
import stat
import tempfile
from pathlib import Path

REQUIRED_CADDY_KEYS = ("PANEL_BASIC_AUTH_USER", "PANEL_BASIC_AUTH_HASH")


def _unique_assignment(lines: list[str], key: str, *, path: Path) -> tuple[int | None, str]:
    matches: list[tuple[int, str]] = []
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            matches.append((index, line[len(prefix) :]))
    if len(matches) > 1:
        raise ValueError(f"duplicate {key} assignments in {path}")
    if not matches:
        return None, ""
    return matches[0]


def _require_value(lines: list[str], key: str, *, path: Path) -> tuple[int | None, str]:
    index, raw_value = _unique_assignment(lines, key, path=path)
    normalized = raw_value.strip()
    if not normalized or normalized in {"''", '""'}:
        raise ValueError(f"{key} is missing or empty in {path}")
    if "\n" in raw_value or "\r" in raw_value:
        raise ValueError(f"{key} contains a newline in {path}")
    return index, raw_value


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def sync_caddy_env(source: Path, target: Path) -> None:
    source = source.resolve(strict=True)
    target = target.resolve(strict=True)
    target_stat = target.stat()
    if stat.S_IMODE(target_stat.st_mode) != 0o600:
        raise PermissionError(f"{target} must have mode 600")

    source_lines = _read_lines(source)
    _, api_key_raw = _require_value(source_lines, "API_KEY", path=source)
    target_lines = _read_lines(target)
    for key in REQUIRED_CADDY_KEYS:
        _require_value(target_lines, key, path=target)
    api_key_index, _ = _unique_assignment(target_lines, "API_KEY", path=target)
    assignment = f"API_KEY={api_key_raw}"
    if api_key_index is None:
        target_lines.append(assignment)
    else:
        target_lines[api_key_index] = assignment
    rendered = "\n".join(target_lines) + "\n"

    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.sync-",
        dir=target.parent,
    )
    try:
        os.fchmod(temp_fd, 0o600)
        if os.geteuid() == 0:
            os.fchown(temp_fd, target_stat.st_uid, target_stat.st_gid)
        with os.fdopen(temp_fd, "w", encoding="utf-8", newline="\n") as temp_file:
            temp_fd = -1
            temp_file.write(rendered)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, target)
        temp_name = ""
        directory_fd = os.open(
            target.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    args = parser.parse_args()
    sync_caddy_env(args.source, args.target)
    print(f"Caddy environment synchronized atomically: {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
