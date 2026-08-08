#!/usr/bin/env python3
"""Entrypoint for the dedicated money-lane mutation worker."""

from __future__ import annotations

import asyncio
import logging

from apps.autopause_worker.main import main_loop
from core.worker_lock import acquire_singleton_lock

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("autopause")
    asyncio.run(main_loop())
