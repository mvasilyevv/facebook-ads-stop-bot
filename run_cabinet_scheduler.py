# -*- coding: utf-8 -*-
"""Container entrypoint для cabinet_scheduler."""

from __future__ import annotations

import asyncio
import logging

from apps.cabinet_scheduler.main import _get_database_url, main_loop
from core.worker_lock import acquire_singleton_lock, run_postgres_singleton
from core.worker_metrics import start_worker_metrics_server

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("cabinet_scheduler")
    start_worker_metrics_server("cabinet_scheduler")
    database_url = _get_database_url()
    asyncio.run(
        run_postgres_singleton(
            "cabinet_scheduler",
            lambda: main_loop(database_url),
            database_url=database_url,
        )
    )
