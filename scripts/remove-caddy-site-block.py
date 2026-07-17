#!/usr/bin/env python3
"""Remove one exact top-level Caddy site block from a Caddyfile atomically."""

from __future__ import annotations

import argparse
import os
import re
import stat
import tempfile
from pathlib import Path


class CaddyBlockError(ValueError):
    """The target Caddyfile cannot be transformed safely."""


def remove_site_block(content: str, site: str) -> tuple[str, bool]:
    """Remove exactly one top-level ``site { ... }`` block, preserving all else."""
    pattern = re.compile(rf"^(?P<indent>[ \t]*){re.escape(site)}[ \t]*\{{[ \t]*$", re.MULTILINE)
    matches = list(pattern.finditer(content))
    top_level = [match for match in matches if not match.group("indent")]
    if not top_level:
        return content, False
    if len(top_level) != 1:
        raise CaddyBlockError(f"found {len(top_level)} top-level blocks for {site}")

    match = top_level[0]
    line_start = match.start()
    depth = 0
    in_quote = False
    escaped = False
    block_end: int | None = None
    for index in range(match.start(), len(content)):
        char = content[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_quote:
            escaped = True
            continue
        if char == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                block_end = index + 1
                if block_end < len(content) and content[block_end] == "\n":
                    block_end += 1
                break
            if depth < 0:
                raise CaddyBlockError(f"unbalanced braces while removing {site}")
    if block_end is None or depth != 0 or in_quote:
        raise CaddyBlockError(f"unterminated block for {site}")

    # Consume one separator line so repeated deployments do not accumulate gaps.
    if block_end < len(content) and content[block_end : block_end + 1] == "\n":
        block_end += 1
    return content[:line_start] + content[block_end:], True


def update_file(path: Path, site: str) -> bool:
    original = path.read_text(encoding="utf-8")
    updated, changed = remove_site_block(original, site)
    if not changed:
        return False
    metadata = path.stat()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, stat.S_IMODE(metadata.st_mode))
        try:
            os.chown(temporary_name, metadata.st_uid, metadata.st_gid)
        except PermissionError:
            pass
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("site")
    args = parser.parse_args()
    update_file(args.path, args.site)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
