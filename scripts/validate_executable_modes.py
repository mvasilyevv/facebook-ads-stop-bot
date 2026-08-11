#!/usr/bin/env python3
"""Validate executable entrypoints from Git's index, not host filesystem modes."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def indexed_modes(root: Path) -> dict[str, str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--stage", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"cannot inspect Git index: {detail}")
    modes: dict[str, str] = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, encoded_path = record.split(b"\t", 1)
        mode = metadata.split(b" ", 1)[0].decode("ascii")
        modes[encoded_path.decode("utf-8")] = mode
    return modes


def validate(root: Path, entrypoints: list[str]) -> list[str]:
    modes = indexed_modes(root)
    errors: list[str] = []
    for entrypoint in entrypoints:
        mode = modes.get(entrypoint)
        if mode is None:
            errors.append(f"{entrypoint}: not tracked")
        elif mode != "100755":
            errors.append(f"{entrypoint}: expected Git mode 100755, found {mode}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("entrypoint", nargs="+")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = validate(args.root.resolve(), args.entrypoint)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Executable mode contract: OK ({len(args.entrypoint)} entrypoints)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
