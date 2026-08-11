#!/usr/bin/env python3
"""Report whether the current database has a valid adoption receipt."""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from core.adoption.service import AdoptionReceiptSnapshot, inspect_adoption_receipt
from core.config import get_settings

RECEIPT_PRESENT = 0
RECEIPT_ERROR = 1
RECEIPT_ABSENT = 3


async def _receipt_status() -> AdoptionReceiptSnapshot | None:
    engine = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        return await inspect_adoption_receipt(engine)
    finally:
        await engine.dispose()


def main() -> int:
    try:
        receipt = asyncio.run(_receipt_status())
    except Exception:
        print("adoption receipt status failed", file=sys.stderr)
        return RECEIPT_ERROR
    if receipt is None:
        print("adoption receipt absent", file=sys.stderr)
        return RECEIPT_ABSENT
    print(f"adoption receipt valid; imported_at={receipt.imported_at.isoformat()}")
    return RECEIPT_PRESENT


if __name__ == "__main__":
    raise SystemExit(main())
