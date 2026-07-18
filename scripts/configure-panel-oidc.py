#!/usr/bin/env python3
"""Atomically add Telegram panel OIDC credentials to a root-only dotenv file.

The client secret is read from stdin so it never appears in shell history or
process arguments. The script intentionally never prints the secret.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
from pathlib import Path


def _validate_single_line(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized or "\n" in normalized or "\r" in normalized:
        raise ValueError(f"{name} must be a non-empty single-line value")
    return normalized


def _upsert(lines: list[str], key: str, value: str) -> None:
    prefix = f"{key}="
    matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(matches) > 1:
        raise ValueError(f"duplicate {key} assignments")
    assignment = f"{key}={value}"
    if matches:
        lines[matches[0]] = assignment
    else:
        lines.append(assignment)


def configure(path: Path, *, client_id: str, client_secret: str, redirect_uri: str) -> None:
    path = path.resolve(strict=True)
    source_stat = path.stat()
    if stat.S_IMODE(source_stat.st_mode) != 0o600:
        raise PermissionError(f"{path} must have mode 600")

    client_id = _validate_single_line("client id", client_id)
    client_secret = _validate_single_line("client secret", client_secret)
    redirect_uri = _validate_single_line("redirect URI", redirect_uri)
    if not client_id.isdigit():
        raise ValueError("client id must be numeric")
    if len(client_secret) < 32:
        raise ValueError("client secret is unexpectedly short")
    if not redirect_uri.startswith("https://"):
        raise ValueError("redirect URI must use HTTPS")

    lines = path.read_text(encoding="utf-8").splitlines()
    _upsert(lines, "TELEGRAM_OIDC_CLIENT_ID", client_id)
    _upsert(lines, "TELEGRAM_OIDC_CLIENT_SECRET", client_secret)
    _upsert(lines, "TELEGRAM_OIDC_REDIRECT_URI", redirect_uri)
    rendered = "\n".join(lines) + "\n"

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.oidc-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        if os.geteuid() == 0:
            os.fchown(fd, source_stat.st_uid, source_stat.st_gid)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            fd = -1
            output.write(rendered)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
        temp_name = ""
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--redirect-uri", required=True)
    args = parser.parse_args()
    configure(
        args.env_file,
        client_id=args.client_id,
        client_secret=sys.stdin.read(),
        redirect_uri=args.redirect_uri,
    )
    print("Telegram panel OIDC credentials configured atomically")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
