# -*- coding: utf-8 -*-
"""Container entrypoint для campaign_creator_worker."""

from __future__ import annotations

import asyncio
import logging

from apps.campaign_creator_worker.main import main_loop
from core.worker_lock import acquire_singleton_lock
from core.worker_metrics import start_worker_metrics_server

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("campaign_creator")
    start_worker_metrics_server("campaign_creator")
    asyncio.run(main_loop())
