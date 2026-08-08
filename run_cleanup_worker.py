# -*- coding: utf-8 -*-
"""Container entrypoint для cleanup_worker."""

from __future__ import annotations

import asyncio
import logging

from apps.cleanup_worker.main import _get_database_url, main_loop
from core.worker_lock import acquire_singleton_lock, run_postgres_singleton
from core.worker_metrics import start_worker_metrics_server

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("cleanup")
    start_worker_metrics_server("cleanup")
    database_url = _get_database_url()
    asyncio.run(
        run_postgres_singleton(
            "cleanup",
            lambda: main_loop(database_url),
            database_url=database_url,
        )
    )
