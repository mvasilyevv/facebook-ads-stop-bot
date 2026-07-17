#!/usr/bin/env python3
"""Build the deterministic, resource-only Guacamole extension archive."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def build(source: Path, output: Path) -> None:
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files or not (source / "guac-manifest.json").is_file():
        raise SystemExit("Guacamole extension source is incomplete")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.source, args.output)


if __name__ == "__main__":
    main()
