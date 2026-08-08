# -*- coding: utf-8 -*-
"""Container entrypoint для reconciler_worker."""

from __future__ import annotations

import asyncio
import logging

from apps.reconciler_worker.main import _get_database_url, main_loop
from core.worker_lock import acquire_singleton_lock, run_postgres_singleton

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("reconciler")
    database_url = _get_database_url()
    asyncio.run(
        run_postgres_singleton(
            "reconciler",
            lambda: main_loop(database_url),
            database_url=database_url,
        )
    )
