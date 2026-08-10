#!/usr/bin/env python3
"""Idempotently adopt reviewed configuration into the clean first release."""

from __future__ import annotations

import asyncio
import stat
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from core.adoption.bundle import parse_adoption_bundle_json
from core.adoption.cli import MAX_BUNDLE_BYTES
from core.adoption.service import adopt_first_release_bundle
from core.config import get_settings

BUNDLE_PATH = Path("/run/fb-agent/adoption-bundle-v1.json")


def _read_bundle():
    source_stat = BUNDLE_PATH.stat(follow_symlinks=False)
    if not stat.S_ISREG(source_stat.st_mode):
        raise RuntimeError("adoption bundle must be a regular file")
    if source_stat.st_size > MAX_BUNDLE_BYTES:
        raise RuntimeError("adoption bundle exceeds the size limit")
    return parse_adoption_bundle_json(BUNDLE_PATH.read_bytes())


async def _run() -> None:
    bundle = _read_bundle()
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        result = await adopt_first_release_bundle(engine, bundle=bundle)
    finally:
        await engine.dispose()
    outcome = "imported" if result.imported else "verified"
    print(f"first-release adoption {outcome}; source_fingerprint={result.source_fingerprint}")


def main() -> int:
    try:
        asyncio.run(_run())
    except Exception:
        print("first-release adoption failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
