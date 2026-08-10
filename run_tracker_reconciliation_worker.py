# -*- coding: utf-8 -*-
"""Container entrypoint для tracker_reconciliation_worker."""

from __future__ import annotations

import asyncio
import logging

from apps.tracker_reconciliation_worker.main import _get_database_url, main_loop
from core.worker_lock import acquire_singleton_lock
from core.worker_metrics import start_worker_metrics_server

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("tracker_reconciliation_worker")
    start_worker_metrics_server("tracker_reconciliation_worker")
    asyncio.run(main_loop(_get_database_url()))
